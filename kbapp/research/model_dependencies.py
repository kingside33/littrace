"""Shared semantic model dependencies for resumable research artifacts."""
import json
from kbapp.core.io import digest
from kbapp.integrations import local_model, cloud


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False))


def model_dependencies(store, *, local, purpose):
    cfg = store.settings()
    if local:
        from kbapp.core.config import OLLAMA
        from kbapp.integrations.model_identity import signature
        system = (local_model.CLASSIFICATION_SYSTEM if purpose == 'lineage_classification' else
                  local_model.CLAIM_SYSTEM if purpose == 'understanding_verify' else local_model.SYSTEM)
        return dict(origin='local', identity=signature(cfg['local_text_model'], OLLAMA),
                    context=cfg['local_text_context'], system=system, purpose=purpose)
    return dict(origin='cloud', model=cfg['model'], api_base=cfg.get('api_base'), system=cloud.SYSTEM, purpose=purpose)


def extraction_fingerprint(store, prompt, purpose, version):
    return fingerprint(dict(version=version, prompt=prompt,
                            model=model_dependencies(store, local=store.settings().get('relation_extractor') == 'local', purpose=purpose)))
