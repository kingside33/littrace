"""kbapp.library.catalog：由职责边界组织的知识库能力。"""
import json

def enriched_papers(store):
    papers = store.papers()
    with store.db() as db:
        classifications = db.execute('SELECT * FROM paper_classifications').fetchall()
        mappings = db.execute('SELECT * FROM paper_methods').fetchall()
    for row in classifications:
        p = papers.get(row['paper_id'])
        data = json.loads(row['payload'])
        if p:
            p['classification'] = data
            if p.get('kind') != 'library':
                for field in ('directions', 'project', 'project_stages'):
                    if row['manual'] or (not p.get(field) and p.get('project_assignment') != 'manual'):
                        p[field] = data.get(field, p.get(field))
    for row in mappings:
        if row['paper_id'] in papers:
            papers[row['paper_id']].setdefault('methods', []).append(row['method_id'])
    return papers
