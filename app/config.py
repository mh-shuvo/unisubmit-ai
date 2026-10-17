from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    app_secret:str='change-this-secret-in-production'
    database_url:str='sqlite:///./unisubmit.db'
    ai_service_url:str='http://127.0.0.1:8001'
    ollama_model:str='qwen3:32b'
    smtp_host:str=''
    smtp_port:int=587
    smtp_username:str=''
    smtp_password:str=''
    smtp_from:str='unisubmit@example.edu'
    smtp_use_tls:bool=True
    model_config=SettingsConfigDict(env_file='.env',extra='ignore')
settings=Settings()
