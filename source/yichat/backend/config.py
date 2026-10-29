from dataclasses import dataclass
from pathlib import Path
import os

@dataclass
class Settings:
    root: Path = Path(os.getenv('YICHAT_DATA', '/var/lib/yichat'))
    site: Path = Path(os.getenv('YICHAT_SITE', '/root/yitiao'))
    origin: str = os.getenv('YICHAT_ORIGIN', 'https://chat.yitiao.org')
    synapse: str = os.getenv('YICHAT_SYNAPSE', 'http://127.0.0.1:8018')
    # Changed only by an explicit chat reset, never by routine deployments.
    message_epoch: str = 'plain-20261029-v1'
    server_name: str = 'chat.yitiao.org'
    issuer: str = 'https://yitiao.org'
    idp_internal: str = os.getenv('YICHAT_IDP_INTERNAL', 'http://127.0.0.1:5001')
    admin_token: str = os.getenv('YICHAT_SYNAPSE_ADMIN_TOKEN', '')
    session_key: str = os.getenv('YICHAT_SESSION_KEY', '')
    fcm_project: str = os.getenv('YICHAT_FCM_PROJECT', '')
    fcm_credentials: str = os.getenv('YICHAT_FCM_CREDENTIALS', '')
    official: str = os.getenv('YICHAT_OFFICIAL_WALLET', '')
    funds_enabled: bool = os.getenv('YICHAT_FUNDS_ENABLED', 'false') == 'true'
    upload_rate: int = int(os.getenv('YICHAT_UPLOAD_RATE', '500000'))
    upload_concurrency: int = int(os.getenv('YICHAT_UPLOAD_CONCURRENCY', '3'))
    max_file: int = 100000000
    archive_reader: str = os.getenv('YICHAT_ARCHIVE_READER_DSN', '')
    archive_ingest: str = os.getenv('YICHAT_ARCHIVE_INGEST_DSN', '')
    archive_sealer: str = os.getenv('YICHAT_ARCHIVE_SEALER_DSN', '')
    archive_cleanup: str = os.getenv('YICHAT_ARCHIVE_CLEANUP_DSN', '')
    archive_path: Path = Path(os.getenv('YICHAT_ARCHIVE_PATH', '/var/lib/yichat/archives'))
    daily_bytes: int = int(os.getenv('YICHAT_DAILY_UPLOAD_BYTES', '1073741824'))
    packet_ttl: int = int(os.getenv('YICHAT_PACKET_TTL_SECONDS', '86400'))
    transfer_ttl: int = int(os.getenv('YICHAT_TRANSFER_TTL_SECONDS', '86400'))

    def __post_init__(self):
        for name in ('root','site','archive_path'):setattr(self,name,Path(getattr(self,name)))
        for name in ('upload_rate','upload_concurrency','max_file','daily_bytes','packet_ttl','transfer_ttl'):
            if getattr(self,name)<=0: raise ValueError(name+' must be positive')

    @property
    def db(self): return self.root / 'chat.db'
    @property
    def objects(self): return self.root / 'objects'
    @property
    def ledger(self): return self.site / 'userdata.db'
    @property
    def accounts(self): return self.site / 'account.db'
