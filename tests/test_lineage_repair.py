"""首次补充的身份错误、证据交接与恢复边界回归；模型均为模拟。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kbapp.storage.store import Store
from kbapp.core.io import now
from kbapp.research.evidence import artifact, latest
from kbapp.research import lineage, lineage_material
from kbapp.workflows import agents, agent_units
from kbapp.workflows.lineage_repair import repair


class LineageRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store.save_settings(
            {'model': 'test-cloud-model', 'local_text_model': 'test-local-model'}
        )
        identity = patch(
            'kbapp.integrations.model_identity.signature',
            return_value={'model': 'test', 'revision': 'weights'},
        )
        identity.start()
        self.addCleanup(identity.stop)
        self.a = dict(
            id='a',
            title='New method',
            kind='library',
            citekey='A',
            abstract='An efﬁcient\n sampling method.',
            year=2024,
        )
        self.b = dict(
            id='b',
            title='Target algorithm',
            kind='library',
            citekey='B',
            abstract='Fixed sampling.',
            year=2020,
        )
        self.store.put_papers([self.a, self.b])
        self.context = dict(
            paper=self.a,
            source=dict(
                id='abstract:a:old', paper_id='a', kind='metadata', text=self.a['abstract']
            ),
            fingerprint='fp',
        )
        self.relation = dict(
            src='a',
            dst='b',
            kind='improves',
            explanation='Improves Target',
            previous_problem='Fixed sampling',
            change='Adaptive sampling',
            conditions='Static maps',
            evidence=[],
        )
        self.sa = dict(
            id='sa',
            paper_id='a',
            kind='paper_text',
            text='We improve Target [1] by adding adaptive sampling.',
            page_start=1,
            section='Method',
        )
        self.sb = dict(
            id='sb',
            paper_id='b',
            kind='paper_text',
            text='The Target algorithm uses fixed sampling.',
            page_start=1,
            section='Method',
        )
        self.cite = dict(
            src='a',
            dst='b',
            kind='cites',
            status='confirmed',
            level='citation',
            evidence=[{'ref_index': '1'}],
        )

    def frozen(self):
        return dict(
            token='sources-v1',
            papers={'a': self.a, 'b': self.b},
            sources={'sa': self.sa, 'sb': self.sb},
            grouped={'a': [self.sa], 'b': [self.sb]},
            relations=[self.cite],
        )

    def extraction(self):
        return {
            'relation': dict(
                self.relation,
                evidence=[
                    {'source_id': 'sa', 'quote': self.sa['text']},
                    {'source_id': 'sb', 'quote': self.sb['text']},
                ],
            )
        }

    def verdict(self, store, prompt, purpose, **kwargs):
        item = json.loads(prompt.split('\n', 1)[1])['claims'][0]
        return {
            'reviews': [
                dict(
                    id=item['id'],
                    supported=True,
                    decision='supported',
                    method_relation=True,
                    unsupported_details=[],
                    reason='Both originals support this method relation.',
                )
            ]
        }

    def test_only_exact_legacy_source_identity_can_be_repaired(self):
        self.assertEqual(
            lineage_material.fixed_relation(
                self.context, self.b, dict(self.relation, src='abstract:a:old')
            )['src'],
            'a',
        )
        for source in ('abstract:someone-else', 'other', None):
            with self.assertRaisesRegex(ValueError, '结构错误'):
                lineage_material.fixed_relation(
                    self.context, self.b, dict(self.relation, src=source)
                )
        self.assertEqual(
            lineage_material.fixed_relation(
                self.context, self.b, {'kind': 'improves'}, legacy=False
            )['dst'],
            'b',
        )
        self.assertNotIn(
            'id',
            lineage_material.fixed_relation(
                self.context, self.b, dict(self.relation, id='another-relation')
            ),
        )

    def test_classification_reuses_saved_model_response_and_preserves_literal_quote(self):
        response = dict(
            directions=['02'],
            project_relevance=True,
            reason='Sampling',
            quote='An efficient sampling method.',
            methods=[],
        )
        with (
            patch('kbapp.library.network.ensure_research_metadata', side_effect=lambda s, p: p),
            patch('kbapp.integrations.cloud.complete', return_value=response) as call,
        ):
            first = lineage.classify_paper(self.store, self.a)
            second = lineage.classify_paper(self.store, self.a)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(first['classification']['quote'], self.a['abstract'])
        self.assertEqual(first['classification'], second['classification'])
        self.assertEqual(
            latest(self.store, 'ClassificationProposal', 'a')['verification_status'], 'candidate'
        )

    def test_invalid_classification_is_saved_without_being_billed_again(self):
        with (
            patch('kbapp.library.network.ensure_research_metadata', side_effect=lambda s, p: p),
            patch(
                'kbapp.integrations.cloud.complete', return_value={'quote': 'Invented words'}
            ) as call,
        ):
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, '不能定位'):
                    lineage.classify_paper(self.store, self.a)
        self.assertEqual(call.call_count, 1)
        self.assertIsNotNone(latest(self.store, 'ClassificationProposal', 'a'))

    def test_pdf_abstract_word_break_uses_cached_quote_and_keeps_original_characters(self):
        paper = dict(
            self.a, abstract='Coverage path plan- ning significantly influences inspection.'
        )
        response = dict(
            directions=['04'],
            project_relevance=True,
            quote='Coverage path planning significantly influences inspection.',
            methods=[],
        )
        with (
            patch('kbapp.library.network.ensure_research_metadata', side_effect=lambda s, p: p),
            patch('kbapp.integrations.cloud.complete', return_value=response) as model,
        ):
            context = lineage.classify_paper(self.store, paper)
            lineage.classify_paper(self.store, paper)
        self.assertEqual(context['classification']['quote'], paper['abstract'])
        self.assertEqual(model.call_count, 1)
        from kbapp.core.text import locate_quote

        self.assertIsNone(locate_quote('alpha- beta', 'alphabeta'))

    def test_missing_fulltext_is_insufficient_and_requests_approval_without_models(self):
        data = self.frozen()
        data['grouped']['b'] = []
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=data),
            patch('kbapp.integrations.local_model.complete_local') as model,
        ):
            material = lineage_material.gather(self.store, self.relation)
        self.assertEqual(material['verification_status'], 'insufficient')
        model.assert_not_called()
        self.assertEqual(self.store.approvals()[0]['paper_id'], 'b')

    def test_prepared_bilateral_evidence_runs_local_and_cloud_review_and_reuses_cache(self):
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=self.frozen()),
            patch(
                'kbapp.library.evidence_sources.refresh_sources',
                side_effect=lambda s, x, **k: (x, []),
            ),
            patch(
                'kbapp.integrations.local_model.complete_local', return_value=self.extraction()
            ) as extract,
        ):
            material = lineage_material.gather(self.store, self.relation)
            self.assertEqual(material['verification_status'], 'ready')
            self.assertEqual(
                lineage_material.gather(self.store, self.relation)['id'], material['id']
            )
            self.assertEqual(extract.call_count, 1)
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=self.frozen()),
            patch(
                'kbapp.integrations.local_model.complete_local', side_effect=self.verdict
            ) as local,
            patch('kbapp.integrations.cloud.complete', side_effect=self.verdict) as cloud,
        ):
            first = lineage_material.assess(self.store, material, 'a|b')
            second = lineage_material.assess(self.store, material, 'a|b')
        self.assertEqual(first['verification_status'], 'verified')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(local.call_count, 1)
        self.assertEqual(cloud.call_count, 1)
        self.assertTrue(
            any(
                r['status'] == 'confirmed' and r['kind'] == 'improves'
                for r in self.store.relations()
            )
        )
        confirmed = [
            r
            for r in self.store.relations()
            if r['status'] == 'confirmed' and r['kind'] == 'improves'
        ][0]
        self.assertTrue(confirmed.get('reviewed_by'), 'B05：自动确认的原文关系必须携带复核者标识')
        self.assertEqual(
            confirmed['evidence_grade']
            if 'evidence_grade' in confirmed
            else __import__(
                'kbapp.research.viewmodels', fromlist=['relation_grade']
            ).relation_grade(confirmed),
            'fulltext',
        )

    def test_supplement_does_not_relax_target_citation_requirement(self):
        data = self.frozen()
        data['relations'] = []
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=data),
            patch(
                'kbapp.library.evidence_sources.refresh_sources',
                side_effect=lambda s, x, **k: (x, []),
            ),
            patch('kbapp.integrations.local_model.complete_local', return_value=self.extraction()),
        ):
            material = lineage_material.gather(self.store, self.relation)
        self.assertEqual(material['verification_status'], 'insufficient')
        with patch('kbapp.integrations.cloud.complete') as call:
            result = lineage_material.assess(self.store, material, 'a|b')
        call.assert_not_called()
        self.assertEqual(result['verification_status'], 'insufficient')

    def test_model_selects_original_sentences_without_retyping_quotes(self):
        def select(store, prompt, purpose, **kwargs):
            data = json.loads(prompt.split('\n', 1)[1])
            refs = [{'sentence_id': source['sentences'][0]['id']} for source in data['sources']]
            return {'relation': dict(self.relation, evidence=refs)}

        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=self.frozen()),
            patch(
                'kbapp.library.evidence_sources.refresh_sources',
                side_effect=lambda s, x, **k: (x, []),
            ),
            patch('kbapp.integrations.local_model.complete_local', side_effect=select),
        ):
            material = lineage_material.gather(self.store, self.relation)
        self.assertEqual(material['verification_status'], 'ready')
        self.assertEqual(
            [e['quote'] for e in material['relation']['evidence']],
            [self.sa['text'], self.sb['text']],
        )

    def test_small_context_selects_complete_sentences_from_both_papers(self):
        from kbapp.core.chunking import est_tokens
        from kbapp.integrations.local_model import SYSTEM

        data = self.frozen()
        data['grouped']['a'] = [
            dict(self.sa, text=self.sa['text'] + ' ' + ('Unrelated explanatory background. ' * 120))
        ]
        data['grouped']['b'] = [
            dict(self.sb, text=self.sb['text'] + ' ' + ('Unrelated introductory material. ' * 120))
        ]
        self.store.save_settings({'local_text_context': 4096})

        def select(store, prompt, purpose, **kwargs):
            self.assertLessEqual(est_tokens(SYSTEM + prompt) + kwargs['max_tokens'] + 256, 4096)
            rows = json.loads(prompt.split('\n', 1)[1])['sources']
            self.assertEqual({r['paper_id'] for r in rows}, {'a', 'b'})
            evidence = [{'sentence_id': r['sentences'][0]['id']} for r in rows]
            return {'relation': dict(self.relation, evidence=evidence)}

        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=data),
            patch(
                'kbapp.library.evidence_sources.refresh_sources',
                side_effect=lambda s, x, **k: (x, []),
            ),
            patch('kbapp.integrations.local_model.complete_local', side_effect=select) as model,
        ):
            result = lineage_material.gather(self.store, self.relation)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['verification_status'], 'ready')

    def test_model_failure_preserves_existing_confirmed_and_manual_relations(self):
        rid = self.store.relation(
            dict(self.relation, status='confirmed', level='fulltext', reviewed_by='previous')
        )
        prepared = dict(
            relation=dict(self.relation, id=rid),
            sources={'sa': self.sa, 'sb': self.sb},
            references=[self.cite],
            verification_status='ready',
            input_fingerprint='x',
        )
        with patch(
            'kbapp.research.review.review_relation', side_effect=ValueError('云端调用未完成')
        ):
            with self.assertRaises(ValueError):
                lineage.reassess_relation(self.store, rid, prepared=prepared)
        self.assertEqual(self.store.relations()[0]['status'], 'confirmed')
        self.store.relation(dict(self.relation, id=rid, status='confirmed'), manual=True)
        with patch('kbapp.research.lineage_material.gather') as gather:
            self.assertTrue(lineage.reassess_relation(self.store, rid)['preserved_manual'])
        gather.assert_not_called()

    def test_repair_preview_is_nonmutating_and_apply_is_idempotent_without_model_calls(self):
        run = agents.create_run(self.store, {'kind': 'lineage', 'paper_ids': ['a']}, start=False)
        with self.store.db() as db:
            db.execute(
                'INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    'parent',
                    run['id'],
                    'lineage_researcher',
                    'a',
                    'fp',
                    'complete',
                    '{}',
                    '{}',
                    1,
                    '',
                    now(),
                ),
            )
            db.execute("UPDATE agent_runs SET status='partial' WHERE id=?", (run['id'],))
        artifact(self.store, 'Classification', 'a', 'fp', {'quote': self.a['abstract']}, 'abstract')
        artifact(
            self.store,
            'RelationProposal',
            'a|b',
            'pair',
            {'proposal': {'relation': dict(self.relation, src=self.context['source']['id'])}},
        )
        with (
            patch('kbapp.integrations.cloud.complete') as cloud,
            patch('kbapp.integrations.local_model.complete_local') as local,
        ):
            self.assertEqual(repair(self.store, run['id'])['total'], 1)
            self.assertEqual(repair(self.store, run['id'], apply=True)['scheduled'], 1)
            self.assertEqual(repair(self.store, run['id'], apply=True)['scheduled'], 0)
        cloud.assert_not_called()
        local.assert_not_called()
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM agent_units WHERE stage LIKE 'gather-evidence:repair:%'"
                ).fetchone()[0],
                1,
            )

    def test_duplicate_reference_labels_are_preserved_but_not_bound(self):
        from kbapp.library.documents import index_citations

        with self.store.db() as db:
            db.execute(
                'INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                ('doc', 'a', '', 'hash', 'unused', 1, '{}', now()),
            )
            text = 'We use [1].\nReferences\n[1] First method 10.1234/first\n[1] Second method 10.1234/second\n'
            db.execute(
                'INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)',
                ('doc', 1, 'NATIVE_TEXT', 'complete', text, '', '{}', now()),
            )
        index_citations(self.store, {'id': 'doc', 'paper_id': 'a'})
        with self.store.db() as db:
            refs = db.execute('SELECT target_paper_id,payload FROM document_references').fetchall()
            self.assertEqual(len(refs), 2)
            self.assertTrue(
                all(
                    json.loads(r['payload'])['ambiguous'] and r['target_paper_id'] is None
                    for r in refs
                )
            )
            self.assertEqual(db.execute('SELECT count(*) FROM citation_contexts').fetchone()[0], 0)
        index_citations(self.store, {'id': 'doc', 'paper_id': 'a'})

    def test_source_revision_updates_transactionally_and_snapshot_is_reused(self):
        before = self.store.state('paper_revision', 0)
        self.store.patch_paper('a', {'abstract': 'New abstract'})
        self.assertGreater(self.store.state('paper_revision'), before)
        with (
            patch('kbapp.library.recognition.active_sources', return_value=[]),
            patch('kbapp.library.documents.sources', return_value=[]) as sources,
        ):
            one = lineage_material.snapshot(self.store)
            two = lineage_material.snapshot(self.store)
            self.assertIs(one, two)
            self.assertEqual(sources.call_count, 1)
            self.store.patch_paper('a', {'directions': ['04']})
            lineage_material.snapshot(self.store)
            self.assertEqual(sources.call_count, 1, 'Metadata-only changes rebuilt all PDF sources')
            self.store.set_state('active_version', 'new')
            self.assertIsNot(one, lineage_material.snapshot(self.store))

    def test_changed_original_returns_to_material_stage_before_any_review_call(self):
        from kbapp.research.review import run_phase

        prepared = dict(
            relation=self.relation,
            sources={'sa': self.sa, 'sb': self.sb},
            references=[self.cite],
            source_snapshot='old',
            verification_status='ready',
            input_fingerprint='old',
        )
        with (
            patch(
                'kbapp.research.lineage_material.snapshot',
                return_value=dict(self.frozen(), token='new'),
            ),
            patch(
                'kbapp.library.evidence_sources.refresh_sources',
                return_value=(
                    [dict(self.sa, retrieved_source_id='sa', text='Changed original')],
                    [],
                ),
            ),
            patch('kbapp.integrations.cloud.complete') as cloud,
        ):
            result, next_stage = run_phase(
                'cloud', lambda: lineage_material.assess(self.store, prepared, 'a|b')
            )
        self.assertIsNone(result)
        self.assertEqual(next_stage, 'material')
        cloud.assert_not_called()

    def test_prepared_review_is_not_starved_by_more_material_gathering(self):
        run = agents.create_run(self.store, {'kind': 'lineage', 'paper_ids': ['a']}, start=False)
        with self.store.db() as db:
            parent = dict(id='parent', run_id=run['id'], role='lineage_researcher', subject='a')
            db.execute(
                'INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    'parent',
                    run['id'],
                    'lineage_researcher',
                    'a',
                    'fp',
                    'queued',
                    '{}',
                    '{}',
                    0,
                    '',
                    now(),
                ),
            )
            agent_units.add(db, parent, 'gather-evidence:first', 'retriever', 'first')
            agent_units.add(db, parent, 'gather-evidence:second', 'retriever', 'second')
        first = agent_units.claim(self.store, run['id'])
        agent_units.finish(
            self.store,
            first,
            {},
            [dict(stage='review-local:first', role='verifier', subject='first')],
        )
        self.assertTrue(
            agent_units.claim(self.store, run['id'])['stage'].startswith('review-local')
        )

    def test_new_existing_relation_task_starts_with_durable_material_stage(self):
        run = agents.create_run(self.store, {'kind': 'lineage', 'paper_ids': ['a']}, start=False)
        with self.store.db() as db:
            db.execute(
                'INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    'existing',
                    run['id'],
                    'verifier',
                    'relation:r',
                    'fp',
                    'queued',
                    '{}',
                    '{}',
                    0,
                    '',
                    now(),
                ),
            )
        agent_units.ensure(self.store, run['id'])
        first = agent_units.claim(self.store, run['id'])
        self.assertEqual((first['stage'], first['role']), ('gather-evidence', 'retriever'))

    def test_export_failure_can_retry_without_index_or_model_recomputation(self):
        from kbapp.workflows.publication import sync_exports

        self.store.set_state('active_version', 'index')
        self.store.set_state('last_success', now())
        with (
            patch('kbapp.workflows.notes.publish_notes'),
            patch('kbapp.workflows.reports.publish_lineage'),
            patch('kbapp.workflows.reports.publish_audit'),
            patch('kbapp.workflows.export.export_static', side_effect=[OSError('disk busy'), None]),
            patch('kbapp.integrations.cloud.complete') as cloud,
        ):
            self.assertEqual(sync_exports(self.store)['status'], 'failed')
            self.assertEqual(sync_exports(self.store)['status'], 'synced')
        self.assertEqual(self.store.state('active_version'), 'index')
        cloud.assert_not_called()

    def test_durable_pipeline_reaches_cloud_after_evidence_and_records_summary(self):
        import threading

        run = agents.create_run(self.store, {'kind': 'lineage', 'paper_ids': ['a']}, start=False)
        with self.store.db() as db:
            parent = dict(id='parent', run_id=run['id'], role='lineage_researcher', subject='a')
            db.execute(
                'INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (
                    'parent',
                    run['id'],
                    'lineage_researcher',
                    'a',
                    'fp',
                    'queued',
                    '{}',
                    '{}',
                    0,
                    '',
                    now(),
                ),
            )
            identity = agent_units.add(db, parent, 'identify', 'lineage_researcher', 'a')
            db.execute(
                "UPDATE agent_units SET status='complete',result=? WHERE id=?",
                (json.dumps(self.context), identity),
            )
            proposal = agent_units.add(db, parent, 'propose:b', 'lineage_researcher', 'a|b')
            db.execute(
                "UPDATE agent_units SET status='complete',result=? WHERE id=?",
                (
                    json.dumps(
                        {
                            'proposal': {
                                'relation': dict(self.relation, src=self.context['source']['id'])
                            }
                        }
                    ),
                    proposal,
                ),
            )
            agent_units.add(
                db,
                parent,
                'gather-evidence:old',
                'retriever',
                'a|b',
                dict(
                    context_id=identity,
                    proposal_id=proposal,
                    target=self.b,
                    mode='proposal',
                    cycle=0,
                ),
            )

        def local(store, prompt, purpose, **kwargs):
            return (
                self.extraction()
                if purpose == 'relation_material'
                else self.verdict(store, prompt, purpose, **kwargs)
            )

        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=self.frozen()),
            patch(
                'kbapp.library.evidence_sources.refresh_sources',
                side_effect=lambda s, x, **k: (x, []),
            ),
            patch('kbapp.integrations.local_model.complete_local', side_effect=local),
            patch('kbapp.integrations.cloud.complete', side_effect=self.verdict) as cloud,
            patch('kbapp.workflows.agents.sync_graph'),
        ):
            agents.dispatch(self.store, threading.Event())
        result = agents.get_run(self.store, run['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['verification_counts'].get('verified'), 1)
        self.assertEqual(cloud.call_count, 1)
        with self.store.db() as db:
            stages = [r[0] for r in db.execute('SELECT stage FROM agent_units')]
        self.assertTrue(any(s.startswith('review-cloud:') for s in stages))

    def test_invalid_review_output_is_saved_before_validation_and_not_silently_retried(self):
        from kbapp.research.review import cached_review, retry_deferred
        from kbapp.storage.activity import scope

        claim = dict(id='expected', text='method', evidence=[])
        bad = {'reviews': [dict(id='wrong-paper-id', supported=True, reason='support')]}
        with (
            scope(self.store, 'original-run'),
            patch('kbapp.integrations.cloud.complete', return_value=bad) as model,
        ):
            for _ in range(2):
                with self.assertRaises(ValueError):
                    cached_review(
                        self.store, 'subject', [self.sa], claim, model, False, True, lambda: False
                    )
            self.assertEqual(model.call_count, 1)
            saved = latest(self.store, 'ReviewModelOutput', 'subject')
            self.assertEqual(saved['response'], bad)
            self.assertEqual(saved['expected_claim_id'], 'expected')
            retry_deferred(self.store, 'unrelated-run')
            with self.store.db() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM verification_attempts WHERE status='invalid_output'"
                    ).fetchone()[0],
                    1,
                )
            retry_deferred(self.store, 'original-run')
            with self.store.db() as db:
                self.assertEqual(
                    db.execute('SELECT count(*) FROM verification_attempts').fetchone()[0], 0
                )

    def test_missing_fulltext_check_distinguishes_abstract_only_and_no_abstract(self):
        """F08：仅有摘要与完全无摘要分别记录，不混为同一'证据不足'。"""
        data = self.frozen()
        data['grouped']['b'] = []
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=data),
            patch('kbapp.integrations.local_model.complete_local') as model,
        ):
            material = lineage_material.gather(self.store, self.relation)
        self.assertEqual(material['check'], 'abstract_only')
        model.assert_not_called()
        no_abstract = dict(self.b)
        no_abstract.pop('abstract')
        data = self.frozen()
        data['papers']['b'] = no_abstract
        data['grouped']['b'] = []
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=data),
            patch('kbapp.integrations.local_model.complete_local'),
        ):
            material = lineage_material.gather(
                self.store, dict(self.relation, explanation='Improves Target differently')
            )
        self.assertEqual(material['check'], 'no_abstract')

    def test_direction_conflict_is_recorded_as_structure_error(self):
        """F08：方向与年份冲突单独记录，等待核对前后作身份。"""
        data = self.frozen()
        data['papers']['a'] = dict(self.a, year=2020)
        data['papers']['b'] = dict(self.b, year=2024)
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=data),
            patch('kbapp.integrations.local_model.complete_local') as model,
        ):
            material = lineage_material.gather(self.store, self.relation)
        self.assertEqual(material['check'], 'direction_conflict')
        self.assertEqual(material['verification_status'], 'structure_error')
        model.assert_not_called()

    def test_cache_fingerprint_includes_method_vocabulary(self):
        """F09：方法词表变化后空关系缓存失效并定向重评。"""
        with (
            patch('kbapp.research.lineage_material.snapshot', return_value=self.frozen()),
            patch(
                'kbapp.library.evidence_sources.refresh_sources',
                side_effect=lambda s, x, **k: (x, []),
            ),
            patch(
                'kbapp.integrations.local_model.complete_local', return_value=self.extraction()
            ) as extract,
        ):
            first = lineage_material.gather(self.store, self.relation)
            second = lineage_material.gather(self.store, self.relation)
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(first['id'], second['id'])
            with self.store.db() as db:
                db.execute(
                    'INSERT INTO method_concepts VALUES(?,?,?,?,0,?)',
                    (
                        'method:new',
                        'NewMethod',
                        json.dumps(['new alias']),
                        json.dumps({'family': '测试'}),
                        now(),
                    ),
                )
            third = lineage_material.gather(self.store, self.relation)
        self.assertEqual(extract.call_count, 2)
        self.assertNotEqual(first['id'], third['id'])

    def test_local_first_classification_and_proposal_use_local_model(self):
        """G01：relation_extractor=local 时分类与方法识别走本地模型，不调用云端。"""
        self.store.save_settings({'relation_extractor': 'local'})
        response = dict(
            directions=['02'],
            project_relevance=True,
            reason='Sampling',
            quote=self.a['abstract'],
            methods=[
                {
                    'name': 'Adaptive Sampling',
                    'aliases': ['AS'],
                    'family': '采样与最优规划',
                    'quote': self.a['abstract'],
                }
            ],
        )
        with (
            patch('kbapp.library.network.ensure_research_metadata', side_effect=lambda s, p: p),
            patch('kbapp.integrations.local_model.complete_local', return_value=response) as local,
            patch(
                'kbapp.integrations.cloud.complete',
                side_effect=AssertionError('cloud must not be called'),
            ),
        ):
            context = lineage.classify_paper(self.store, self.a)
        self.assertEqual(context['classification']['directions'], ['02'])
        local.assert_called_once()
        with self.store.db() as db:
            self.assertTrue(
                db.execute(
                    "SELECT 1 FROM method_concepts WHERE name='Adaptive Sampling'"
                ).fetchone()
            )
        proposal_response = {
            'relation': {
                'kind': 'improves',
                'explanation': 'Improves Target',
                'previous_problem': 'Fixed sampling',
                'change': 'Adaptive sampling',
                'conditions': 'Static maps',
                'evidence': [{'source_id': context['source']['id'], 'quote': self.a['abstract']}],
            }
        }
        target = {
            'id': 'b',
            'title': 'Target algorithm',
            'year': 2020,
            'abstract': 'Fixed sampling.',
        }
        with (
            patch(
                'kbapp.integrations.local_model.complete_local', return_value=proposal_response
            ) as local2,
            patch(
                'kbapp.integrations.cloud.complete',
                side_effect=AssertionError('cloud must not be called'),
            ),
        ):
            record = lineage.propose_pair(self.store, context, target)
        self.assertEqual(record['proposal']['relation']['kind'], 'improves')
        self.assertEqual(record['proposal']['relation']['dst'], 'b')
        local2.assert_called_once()

    def test_cloud_extractor_configuration_still_uses_cloud(self):
        """G01：relation_extractor=cloud 时保持原云端路径，本地模型不被调用。"""
        self.store.save_settings({'relation_extractor': 'cloud'})
        response = dict(
            directions=['02'],
            project_relevance=True,
            reason='Sampling',
            quote=self.a['abstract'],
            methods=[],
        )
        with (
            patch('kbapp.library.network.ensure_research_metadata', side_effect=lambda s, p: p),
            patch('kbapp.integrations.cloud.complete', return_value=response) as cloud,
            patch(
                'kbapp.integrations.local_model.complete_local',
                side_effect=AssertionError('local must not be called'),
            ),
        ):
            lineage.classify_paper(self.store, self.a)
        cloud.assert_called_once()

    def test_invalid_local_classification_quote_is_rejected_without_cloud_fallback(self):
        """G01：本地输出无法定位到题摘时报错保留，不静默回退云端。"""
        self.store.save_settings({'relation_extractor': 'local'})
        response = dict(
            directions=['02'],
            project_relevance=True,
            reason='Sampling',
            quote='Invented words',
            methods=[],
        )
        with (
            patch('kbapp.library.network.ensure_research_metadata', side_effect=lambda s, p: p),
            patch('kbapp.integrations.local_model.complete_local', return_value=response),
            patch(
                'kbapp.integrations.cloud.complete',
                side_effect=AssertionError('cloud must not be called'),
            ),
        ):
            with self.assertRaisesRegex(ValueError, '不能定位'):
                lineage.classify_paper(self.store, self.a)


if __name__ == '__main__':
    unittest.main()
