import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]

@dataclass
class Settings:
    runtime: Path=ROOT/'runtime'
    backend: str='synthetic'
    base_url: str='http://127.0.0.1:11435/v1'
    model: str=''
    api_key: str=field(default='local-placeholder',repr=False)
    timeout: float=40
    daily_limit: int=100
    meterless: bool=True
    zetaris_base: str='https://api.saas.enterprise.zetaris.com'
    zetaris_org: str=''
    zetaris_key: str=field(default='',repr=False)
    zetaris_catalog: str='supplytrace'
    zetaris_prefix: str='/api/v1.0'
    zetaris_username: str=field(default='',repr=False)
    zetaris_password: str=field(default='',repr=False)
    dataset_kind: str='original_synthetic_demo'
    dataset_as_of: str='2026-10-22'

    @classmethod
    def from_env(cls):
        # Agent launchers can inherit unrelated provider settings. Project config wins,
        # and process environment credentials are ignored unless explicitly opted in.
        values={} if os.getenv('SUPPLYTRACE_DISABLE_DOTENV')=='true' else dotenv_values(ROOT/'.env',interpolate=False)
        use_process=os.getenv('SUPPLYTRACE_USE_PROCESS_ENV')=='true'
        def get(name,default):
            return values.get(name) if values.get(name) is not None else (os.getenv(name,default) if use_process else default)
        return cls(runtime=Path(os.getenv('SUPPLYTRACE_RUNTIME',str(ROOT/'runtime'))),backend=get('DATA_BACKEND','synthetic'),base_url=get('OPENAI_BASE_URL','http://127.0.0.1:11435/v1'),
            model=get('OPENAI_MODEL',''),api_key=get('OPENAI_API_KEY','local-placeholder'),
            timeout=float(get('MODEL_TIMEOUT_SECONDS','40')),daily_limit=int(get('DAILY_RUN_LIMIT','100')),
            meterless=get('METERLESS_ENABLED','true').lower()=='true',zetaris_base=get('ZETARIS_BASE_URL','https://api.saas.enterprise.zetaris.com'),
            zetaris_org=get('ZETARIS_ORG_ID',''),zetaris_key=get('ZETARIS_API_KEY',''),zetaris_catalog=get('ZETARIS_CATALOG','supplytrace'),
            zetaris_prefix=get('ZETARIS_API_PREFIX','/api/v1.0'),zetaris_username=get('ZETARIS_USERNAME',''),zetaris_password=get('ZETARIS_PASSWORD',''),
            dataset_kind=get('DATASET_KIND','original_synthetic_demo'),dataset_as_of=get('DATASET_AS_OF','2026-10-22'))
