"""Isolate config, authentication and task storage before importing the Hub."""
from contextlib import contextmanager
import copy
import importlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from aw.config import DEFAULTS, load_config, save_config
from aw.auth import AuthManager
from aw.control.broker import Broker
from aw.store import Store
from aw.pusher import MultiIpPusher


@contextmanager
def isolated_hub():
    with tempfile.TemporaryDirectory() as directory:
        config_path = str(Path(directory) / 'config.json')
        data_dir = str(Path(directory) / 'data')
        cfg = copy.deepcopy(DEFAULTS)
        cfg.update(ingest_token='test-receiver', summary_token='test-reader')
        for adapter in cfg['adapters'].values():
            adapter['enabled'] = False
        Path(config_path).write_text(json.dumps(cfg))
        with patch.dict(os.environ, {'AGENT_WATCH_CONFIG':config_path,
                                     'AGENT_WATCH_DATA_DIR':data_dir}):
            hub = importlib.import_module('hub')
            cfg = load_config(config_path)
            with patch.multiple(hub, CFG=cfg, DATA_DIR=data_dir,
                                STORE=Store(cfg, data_dir),
                                CONTROL=Broker(data_dir),
                                AUTH=AuthManager(data_dir, cfg),
                                PUSHER=MultiIpPusher(),
                                save_config=lambda value: save_config(value, config_path)):
                yield hub
