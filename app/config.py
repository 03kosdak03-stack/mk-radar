from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    telegram_bot_token: str = ''
    database_url: str = 'sqlite:///mkval.db'
    admin_user_ids: str = ''
    financial_zip: str = str(ROOT / 'FinancialTable.zip')
    normalized_financials: str = str(ROOT / 'data/normalized_financials.json')
    nominal_share_values: str = str(ROOT / 'data/nominal_share_values.json')
    kap_data_dir: str = str(ROOT / 'data/kap')
    kap_auto_sync: bool = True
    kap_sync_interval_hours: int = 6
    mini_app_url: str = ''
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')

    @property
    def admin_ids(self):
        return {int(x.strip()) for x in self.admin_user_ids.split(',') if x.strip()}

    @property
    def database_path(self):
        if not self.database_url.startswith('sqlite:///'):
            raise ValueError('Bu sürümde sqlite:/// veritabanı yolu gerekli.')
        path = Path(self.database_url.removeprefix('sqlite:///'))
        return path if path.is_absolute() else ROOT / path


settings = Settings()
