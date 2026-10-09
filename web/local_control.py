"""In-process serialized controller for local previews."""
from __future__ import annotations
from copy import deepcopy
from collections import OrderedDict
import time
import threading
from typing import Any, Dict
import uuid
from animation.core.manager import AnimationManager
from ipc.playlist_runtime import PlaylistRunner, normalize_playlist_command
from ipc.runtime_control import controller_activation_coordinator

class LocalControlChannel:
    def __init__(self,manager):
        self.manager=manager
        self.activation_coordinator=controller_activation_coordinator(manager)
        self.last_command_id=0
        self.last_applied_command_id=0
        self._command_results=OrderedDict()
        self._last_result=None
        self._playlist_request_statuses={}
        self._playlist_commands={}
        self.playlist_runner=PlaylistRunner(manager,self.activation_coordinator)
        self._playlist_wake=threading.Event()
        self._playlist_thread=None

    def read_status(self):
        payload=self.manager.get_current_frame()
        payload.update(self.manager.get_current_status())
        payload.update(self.activation_coordinator.controller_status())
        payload.update({'updated_at':time.time(),'last_command_id':self.last_command_id,
            'last_applied_command_id':self.last_applied_command_id,'command_result':deepcopy(self._last_result)})
        return payload

    def send_command(self,action,**data):
        from scripts.start_server import handle_command
        command={'command_id':time.time_ns(),'request_id':str(uuid.uuid4()),'action':action,'data':deepcopy(data)}
        error=None
        try:
            if handle_command(self.manager,action,data) is False:
                error=getattr(self.manager,'_playback_error',None) or 'controller rejected command'
        except (RuntimeError,TypeError,ValueError) as exc:
            error=str(exc)
        result={'request_id':command['request_id'],'command_id':command['command_id'],
            'state':'failed' if error else 'completed','error':error,'completed_at':time.time()}
        self.last_command_id=command['command_id']
        if error is None:
            self.last_applied_command_id=command['command_id']
        self._last_result=result
        self._command_results[command['request_id']]=result
        while len(self._command_results)>512:
            self._command_results.popitem(last=False)
        return command

    def read_command_result(self,request_id):
        return deepcopy(self._command_results.get(request_id))

    def _ensure_playlist_scheduler(self) -> None:
        if self._playlist_thread is not None:
            return
        self._playlist_thread = threading.Thread(
            target=self._run_playlist_scheduler,
            name="ledgrid-local-playlist",
            daemon=True,
        )
        self._playlist_thread.start()


    def _run_playlist_scheduler(self) -> None:
        while True:
            self._playlist_wake.wait(0.05)
            self._playlist_wake.clear()
            self.playlist_runner.advance()


    def enqueue_playlist_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        payload = normalize_playlist_command(command)
        self._ensure_playlist_scheduler()
        existing = self._playlist_commands.get(payload["request_id"])
        if existing is not None and existing != payload:
            raise FileExistsError("playlist request identity already names another command")
        self._playlist_commands[payload["request_id"]] = deepcopy(payload)
        status = self.playlist_runner.dispatch(payload)
        self._playlist_request_statuses[payload["request_id"]] = dict(status)
        self._playlist_wake.set()
        return dict(payload)


    def read_playlist_request_status(self, request_id: str):
        status = self._playlist_request_statuses.get(request_id)
        if status is not None and status.get("run_id") == self.playlist_runner.status().get("run_id"):
            status = self.playlist_runner.status()
        return dict(status) if status is not None else None


    def read_playlist_current_status(self) -> Dict[str, Any]:
        return self.playlist_runner.status()

    def read_playlist_command(self, request_id):
        return deepcopy(self._playlist_commands.get(request_id))
