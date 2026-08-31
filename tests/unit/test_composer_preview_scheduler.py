"""Deterministic race and offline tests for the browser preview scheduler."""

from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCHEDULER = ROOT / "web" / "static" / "js" / "composer_preview_scheduler.js"


class ComposerPreviewSchedulerTests(unittest.TestCase):
    def _node(self, body: str) -> None:
        result = subprocess.run(
            ["node", "-e", textwrap.dedent(body)], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_authored_completion_wins_over_an_older_poll_completion(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          const requests = [], frames = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: (candidate) => {{ const task = deferred(); requests.push({{candidate, task}}); return task.promise; }},
            onFrame: (body, task) => frames.push([body, task.kind]),
            onError: (error) => {{ throw error; }},
            setIntervalFn: () => 1, clearIntervalFn: () => {{}},
          }});
          scheduler.start(() => 'poll');
          scheduler.poll();
          const authored = scheduler.submitAuthored('authored', {{generation: 1}});
          if (requests.length !== 2 || requests[0].candidate !== 'poll' || requests[1].candidate !== 'authored') throw new Error('expected one poll and one authored request');
          requests[1].task.resolve('authored-frame');
          await authored;
          requests[0].task.resolve('stale-poll-frame');
          await flush(); await flush();
          if (JSON.stringify(frames) !== JSON.stringify([['authored-frame', 'authored']])) throw new Error(`stale poll committed: ${{JSON.stringify(frames)}}`);
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)

    def test_polling_is_single_flight_and_stops_after_failure_until_recovery(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          const requests = [], cleared = [], frames = [], errors = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: (candidate) => {{ const task = deferred(); requests.push({{candidate, task}}); return task.promise; }},
            onFrame: (body, task) => frames.push([body, task.kind]), onError: (error, task) => errors.push(task.kind),
            setIntervalFn: () => 7, clearIntervalFn: (id) => cleared.push(id),
          }});
          scheduler.start(() => 'poll');
          scheduler.poll(); scheduler.poll(); scheduler.poll();
          if (requests.length !== 1) throw new Error('poll requests accumulated while one was in flight');
          const offline = new Error('offline'); offline.previewUnavailable = true;
          requests[0].task.reject(offline);
          await flush(); await flush();
          scheduler.poll(); scheduler.poll();
          if (requests.length !== 1 || cleared.length !== 1 || errors.join(',') !== 'poll') throw new Error('failure did not suspend polling');
          const recovery = scheduler.submitAuthored('retry', {{generation: 1}});
          if (requests.length !== 2 || requests[1].candidate !== 'retry') throw new Error('authored retry did not resume safely');
          requests[1].task.resolve('retry-frame');
          await recovery; await flush();
          if (JSON.stringify(frames) !== JSON.stringify([['retry-frame', 'authored']])) throw new Error('recovery committed the wrong frame');
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)

    def test_invalid_scene_error_keeps_polling_available(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          const requests = [], cleared = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: () => {{ const task = deferred(); requests.push(task); return task.promise; }},
            onFrame: () => {{}}, onError: () => {{}},
            setIntervalFn: () => 4, clearIntervalFn: (id) => cleared.push(id),
          }});
          scheduler.start(() => 'poll'); scheduler.poll();
          requests[0].reject(new Error('scene validation failed'));
          await flush(); await flush(); scheduler.poll();
          if (requests.length !== 2 || cleared.length !== 0) throw new Error('validation error incorrectly suspended polling');
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)

    def test_stale_poll_failure_cannot_override_newer_authored_success(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          const requests = [], frames = [], errors = [], cleared = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: (candidate) => {{ const task = deferred(); requests.push({{candidate, task}}); return task.promise; }},
            onFrame: (body, task) => frames.push([body, task.kind]), onError: (error, task) => errors.push(task.kind),
            setIntervalFn: () => 9, clearIntervalFn: (id) => cleared.push(id),
          }});
          scheduler.start(() => 'poll'); scheduler.poll();
          const authored = scheduler.submitAuthored('new', {{generation: 1}});
          requests[1].task.resolve('new-frame'); await authored;
          const offline = new Error('late offline'); offline.previewUnavailable = true;
          requests[0].task.reject(offline); await flush(); await flush();
          if (!scheduler.available || scheduler.timer === null || cleared.length || errors.length) throw new Error('stale poll failure took authority');
          if (JSON.stringify(frames) !== JSON.stringify([['new-frame', 'authored']])) throw new Error('stale poll overwrote authored frame');
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)

    def test_stale_authored_failure_cannot_suspend_newer_authored_recovery(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          const requests = [], frames = [], errors = [], cleared = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: (candidate) => {{ const task = deferred(); requests.push({{candidate, task}}); return task.promise; }},
            onFrame: (body, task) => frames.push([body, task.kind]), onError: (error, task) => errors.push(task.kind),
            setIntervalFn: () => 11, clearIntervalFn: (id) => cleared.push(id),
          }});
          scheduler.start(() => 'poll');
          const first = scheduler.submitAuthored('first', {{generation: 1}});
          const second = scheduler.submitAuthored('second', {{generation: 2}});
          const offline = new Error('old offline'); offline.previewUnavailable = true;
          requests[0].task.reject(offline); await flush(); await flush();
          if (requests.length !== 2 || requests[1].candidate !== 'second') throw new Error('newer authored request was not drained');
          requests[1].task.resolve('second-frame'); await second; await first; await flush();
          if (!scheduler.available || scheduler.timer === null || cleared.length || errors.length) throw new Error('stale authored failure took authority');
          if (JSON.stringify(frames) !== JSON.stringify([['second-frame', 'authored']])) throw new Error('newer authored recovery did not win');
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)

    def test_same_generation_late_poll_failure_yields_to_newer_authored_epoch(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          const requests = [], frames = [], errors = [], cleared = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: (candidate) => {{ const task = deferred(); requests.push({{candidate, task}}); return task.promise; }},
            onFrame: (body, task) => frames.push([body, task.kind, task.generation]), onError: (error, task) => errors.push(task.kind),
            setIntervalFn: () => 13, clearIntervalFn: (id) => cleared.push(id),
          }});
          scheduler.start(() => 'poll');
          const baseline = scheduler.submitAuthored('baseline', {{generation: 7}}); requests[0].task.resolve('baseline-frame'); await baseline; await flush();
          scheduler.poll();
          const newer = scheduler.submitAuthored('newer', {{generation: 7}}); requests[2].task.resolve('newer-frame'); await newer;
          const offline = new Error('late same-generation poll failure'); offline.previewUnavailable = true;
          requests[1].task.reject(offline); await flush(); await flush();
          if (!scheduler.available || scheduler.timer === null || cleared.length || errors.length) throw new Error('same-generation stale poll took authority');
          if (JSON.stringify(frames) !== JSON.stringify([['baseline-frame', 'authored', 7], ['newer-frame', 'authored', 7]])) throw new Error('same-generation stale poll committed');
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)

    def test_same_generation_stale_authored_failure_yields_to_newer_authored_epoch(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          const requests = [], frames = [], errors = [], cleared = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: (candidate) => {{ const task = deferred(); requests.push({{candidate, task}}); return task.promise; }},
            onFrame: (body, task) => frames.push([body, task.kind, task.generation]), onError: (error, task) => errors.push(task.kind),
            setIntervalFn: () => 15, clearIntervalFn: (id) => cleared.push(id),
          }});
          scheduler.start(() => 'poll');
          const first = scheduler.submitAuthored('first', {{generation: 7}});
          const newer = scheduler.submitAuthored('newer', {{generation: 7}});
          const offline = new Error('late same-generation authored failure'); offline.previewUnavailable = true;
          requests[0].task.reject(offline); await flush(); await flush();
          requests[1].task.resolve('newer-frame'); await newer; await first; await flush();
          if (!scheduler.available || scheduler.timer === null || cleared.length || errors.length) throw new Error('same-generation stale authored failure took authority');
          if (JSON.stringify(frames) !== JSON.stringify([['newer-frame', 'authored', 7]])) throw new Error('same-generation newer authored frame did not win');
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)

    def test_hidden_page_skips_polling_and_visible_page_resumes_one_lane(self) -> None:
        self._node(f"""
          (async () => {{
          const {{ComposerPreviewScheduler}} = require({str(SCHEDULER)!r});
          const deferred = () => {{ let resolve, reject; const promise = new Promise((a,b) => {{resolve=a; reject=b}}); return {{promise, resolve, reject}}; }};
          const flush = () => new Promise((resolve) => setImmediate(resolve));
          let visible = false; const requests = [], frames = [], errors = [];
          const scheduler = new ComposerPreviewScheduler({{
            request: () => {{ const task = deferred(); requests.push(task); return task.promise; }},
            onFrame: (body, task) => frames.push([body, task.kind]), onError: (error, task) => errors.push(task.kind),
            isVisible: () => visible, setIntervalFn: () => 17, clearIntervalFn: () => {{}},
          }});
          scheduler.start(() => 'poll'); scheduler.poll(); scheduler.poll();
          if (requests.length || !scheduler.available || scheduler.timer === null) throw new Error('hidden page issued a poll or became unavailable');
          visible = true; scheduler.poll(); scheduler.poll();
          if (requests.length !== 1) throw new Error('visible recovery did not use one single-flight poll');
          requests[0].resolve('visible-frame'); await flush(); await flush();
          if (JSON.stringify(frames) !== JSON.stringify([['visible-frame', 'poll']]) || errors.length) throw new Error('visible recovery did not commit cleanly');
          }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
        """)


if __name__ == "__main__":
    unittest.main()
