#!/usr/bin/env python3
"""Loopback Composer fixture for focused browser smoke checks."""
from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from animation.core.manager import AnimationManager, PreviewLEDController
from web.app import AnimationWebInterface

ROOT = Path(__file__).resolve().parents[2]

class NoWallControlChannel:
    def __init__(self, state_dir: Path):
        self.attempts: list[dict[str, Any]] = []
    def read_status(self):
        return {'controller_session_id':'browser-fixture', 'controller_state_revision':0,
                'is_running':False, 'brightness':0, 'actual_fps':0, 'target_fps':150}
    def send_command(self, action: str, **data: Any):
        self.attempts.append({'action':action, **data})
        raise RuntimeError('Browser fixture has no installed output')
    def read_playlist_current_status(self):
        return {'phase':'stopped', 'run_id':None}
    def read_playlist_request_status(self, request_id):
        return None

def create_fixture_server(state_dir: Path, *, host='127.0.0.1', port=8765):
    if host not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('Browser fixture must bind loopback')
    state_dir = Path(state_dir); state_dir.mkdir(parents=True, exist_ok=True)
    manager = AnimationManager(PreviewLEDController(33,138), auto_start=False)
    channel = NoWallControlChannel(state_dir)
    interface = AnimationWebInterface(channel, manager, local_mode=True, project_root=ROOT,
                                      host=host, port=port, activation_enabled=False)
    # Put all mutable Composer records in the isolated fixture directory.
    from web.working_draft_store import WorkingDraftStore
    from web.scene_look_store import SceneLookStore
    from web.composer_library_state import ComposerLibraryState
    from web.composer_playlist_store import ComposerPlaylistStore
    interface.working_draft = WorkingDraftStore(state_dir / 'draft.json')
    interface.composer_looks = SceneLookStore(state_dir / 'looks.json')
    interface.composer_library = ComposerLibraryState(state_dir / 'library.json')
    interface.composer_playlists = ComposerPlaylistStore(state_dir / 'playlists.json')
    return interface, channel, 'browser-fixture'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    interface,_,_=create_fixture_server(args.state_dir,port=args.port)
    interface.run()
