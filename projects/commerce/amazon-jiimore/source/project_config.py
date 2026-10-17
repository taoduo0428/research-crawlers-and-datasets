"""Local paths and non-public runtime configuration for the three crawlers."""
from copy import deepcopy
import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = SCRIPT_DIR / 'data'
CONFIG_PATH = SCRIPT_DIR / 'config.local.json'
DEFAULT_CONFIG = {
    'proxy': 'http://127.0.0.1:7897',
    'request_timeout': 30,
    'jiimore': {'phone_num': '', 'password': ''},
}


def load_config(path=None):
    """Read optional local settings without exposing credential values."""
    config = deepcopy(DEFAULT_CONFIG)
    path = Path(path) if path is not None else CONFIG_PATH
    if path.exists():
        try:
            with path.open(encoding='utf-8-sig') as handle:
                supplied = json.load(handle)
        except (OSError, ValueError) as error:
            raise ValueError('config.local.json 无法读取，请检查文件和 JSON 格式。') from error
        if not isinstance(supplied, dict):
            raise ValueError('config.local.json 顶层必须是 JSON 对象。')
        config.update({key: value for key, value in supplied.items() if key != 'jiimore'})
        if 'jiimore' in supplied:
            if not isinstance(supplied['jiimore'], dict):
                raise ValueError('config.local.json 中 jiimore 必须是 JSON 对象。')
            config['jiimore'].update(supplied['jiimore'])
    if not isinstance(config['proxy'], str):
        raise ValueError('proxy 必须是字符串；直连请填空字符串。')
    timeout = config['request_timeout']
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError('request_timeout 必须是大于 0 的秒数。')
    for key in ('phone_num', 'password'):
        if not isinstance(config['jiimore'].get(key), str):
            raise ValueError('极目账号与密码必须使用字符串。')
    return config


def get_proxies(config):
    proxy = config['proxy'].strip()
    if not proxy:
        return {}
    if '://' not in proxy:
        proxy = 'http://' + proxy
    return {'http': proxy, 'https': proxy}
