"""Cross-language proof of canonical context integrity and final composition.

The shared library compiles production receiver sources for this host. It is
local software evidence, never receiver deployment or physical telemetry.
"""
from __future__ import annotations

import ctypes
from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import resolve_vibe
from animation.core.receiver_presentation import (
    CanonicalFinalPresentation, ReceiverPresentationContext, serialize_presentation_context,
)
from web.composer_final_preview import InstalledFinalSceneRuntime

ROOT = Path(__file__).resolve().parents[2]


class CanonicalReceiverPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        directory = Path(cls.tmp.name)
        wrapper = directory / 'wrapper.cpp'
        wrapper.write_text(r'''
#include "ledgrid/receiver_runtime.hpp"
#include "ledgrid/receiver_optics.hpp"
extern "C" {
void* make_runtime() { return new ledgrid::ReceiverRuntime(true); }
void free_runtime(void* p) { delete static_cast<ledgrid::ReceiverRuntime*>(p); }
int command(void* p, const unsigned char* b, unsigned long n) {
  return static_cast<int>(static_cast<ledgrid::ReceiverRuntime*>(p)->process_command(b,n,0));
}
int optics(void* p, unsigned char* rgb, unsigned long n,
           const unsigned char* category, const unsigned char* edge) {
  ledgrid::InstallationProfileViewV1 profile{};
  profile.pixel_count = n / 3;
  profile.category = category; profile.obstacle_edge = edge;
  return ledgrid::apply_canonical_final_presentation(rgb,n,&profile,
    static_cast<ledgrid::ReceiverRuntime*>(p)->active_context());
}
int version(void* p) { return static_cast<ledgrid::ReceiverRuntime*>(p)->active_context().wire_version; }
}
''')
        compiler = shutil.which('clang++') or shutil.which('g++')
        if compiler is None:
            raise RuntimeError('C++ compiler is required for receiver contract verification')
        native = ROOT / 'firmware/esp32'
        library = directory / 'receiver.so'
        sources = ['receiver_runtime.cpp', 'receiver_optics.cpp', 'sha256.cpp', 'startup_animation.cpp', 'animation_pipeline_contract.cpp', 'protocol.cpp']
        subprocess.run([compiler, '-std=c++17', '-O2', '-shared', '-fPIC', '-DLEDGRID_ENABLE_LOCAL_BACKGROUND=1', '-DLEDGRID_ENABLE_INSTALLATION_PROFILES=1', '-DLEDGRID_ENABLE_RECEIVER_NATIVE_MODULES=1', '-I', str(native/'include'), str(wrapper), *(str(native/'src'/name) for name in sources), '-o', str(library)], check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.make_runtime.restype = ctypes.c_void_p
        cls.lib.free_runtime.argtypes = [ctypes.c_void_p]
        cls.lib.command.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        cls.lib.optics.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        cls.lib.version.argtypes = [ctypes.c_void_p]

    def setUp(self):
        self.runtime = self.lib.make_runtime()
        self.addCleanup(lambda: self.lib.free_runtime(self.runtime))
        self.context = ReceiverPresentationContext(bytes.fromhex('aa'*16), 9, 4, 0, resolve_vibe('neutral'), PlantModifierState.empty(), 0, CanonicalFinalPresentation('bb'*32, .3, .5, .5, .25, .5))

    def send(self, payload):
        return self.lib.command(self.runtime, ctypes.create_string_buffer(payload), len(payload))

    def activate(self, context):
        for payload in serialize_presentation_context(context):
            self.assertEqual(self.send(payload), 1)

    def test_version_two_roundtrip_binds_exact_scene_and_double_factors(self):
        begin, setting, commit = serialize_presentation_context(self.context)
        self.assertEqual([value[1] for value in (begin, setting, commit)], [2, 2, 2])
        self.assertEqual(len(setting), 217)
        self.assertEqual(setting[-72:-40], bytes.fromhex('bb'*32))
        self.assertEqual(struct.unpack('>5d', setting[-40:]), (.3, .5, .5, .25, .5))
        self.assertEqual(hashlib.sha256(setting[18:]).digest(), begin[26:])
        self.activate(self.context)
        self.assertEqual(self.lib.version(self.runtime), 2)
        # Replay is idempotent and a stripped v1 packet cannot commit this basis.
        self.activate(self.context)
        self.assertNotEqual(self.send(commit[:1] + b'\x01' + commit[2:]), 1)

    def test_corrupt_truncated_nonfinite_and_overrange_contexts_preserve_prior(self):
        self.activate(replace(self.context, canonical_final=None))
        fresh = replace(self.context, scene_revision=10)
        begin, setting, commit = serialize_presentation_context(fresh)
        for malformed in (setting[:-1], setting[:-1]+bytes([setting[-1]^1])):
            self.assertEqual(self.send(begin), 1)
            self.assertNotEqual(self.send(malformed), 1)
            self.assertEqual(self.lib.version(self.runtime), 1)
        for invalid in (float('nan'), float('inf'), -1.0, 2.01):
            malformed = setting[:-40] + struct.pack('>d', invalid) + setting[-32:]
            forged_begin = begin[:26] + hashlib.sha256(malformed[18:]).digest()
            # Use a new session to avoid the prior staged digest conflict.
            forged_begin = forged_begin[:2] + b'c'*16 + forged_begin[18:]
            malformed = malformed[:2]+b'c'*16+malformed[18:]
            self.assertEqual(self.send(forged_begin), 1)
            self.assertNotEqual(self.send(malformed), 1)
            self.assertEqual(self.lib.version(self.runtime), 1)
            # Reset only the test receiver between intentionally conflicting requests.
            self.lib.free_runtime(self.runtime)
            self.runtime = self.lib.make_runtime()
            self.activate(replace(self.context, canonical_final=None))
        self.assertNotEqual(self.send(commit), 1)

    def test_final_pass_matches_production_preview_rounding_and_overlap(self):
        # Exact half-integers, saturated lift, foliage/globe boundaries and RGB
        # cross-channel mixing exercise the order that algebraic shortcuts lose.
        pixels = np.array([[1,3,5], [127,128,129], [255,251,249], [2,6,10], [13,27,49], [0,1,255]], dtype=np.uint8)
        category = np.array([0,1,2,0,1,2], dtype=np.uint8)
        edge = np.array([0,1,1,0,0,0], dtype=np.uint8)
        geometry = SimpleNamespace(foliage_flat=category==1, globes_flat=category==2, obstacle_edge=edge.astype(bool))
        owner = SimpleNamespace(_installation_geometry=lambda: geometry)
        for brightness in (0, .5, .123456789, 1, 1.75, 2):
            context = replace(self.context, controller_session_id=bytes([int(brightness*100)+1])*16, canonical_final=replace(self.context.canonical_final, brightness=brightness))
            self.activate(context)
            output = pixels.copy()
            self.assertEqual(self.lib.optics(self.runtime, output.ctypes.data, output.nbytes, category.ctypes.data, edge.ctypes.data), 1)
            plants = {'effects': {'strengths': {'shadow': .5, 'illuminate': .25, 'hue_shift': .5}}}
            expected = InstalledFinalSceneRuntime._plant_optics(owner, pixels, plants)
            work = np.empty(expected.shape, dtype=np.float32)
            np.multiply(expected, float(brightness), out=work)
            np.rint(work, out=work); np.clip(work,0,255,out=work)
            np.testing.assert_array_equal(output, work.astype(np.uint8))
