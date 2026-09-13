# Fault-inject the patched, pinned ESP ELF loader source.

from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "firmware/esp32"))

import elf_loader_patch  # noqa: E402


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "esp_elf.h"
#include "private/elf_platform.h"

struct allocation {
    void *pointer;
    bool live;
};
static struct allocation allocations[16];
static int allocation_calls;
static int fail_call;
static int frees;
static int double_frees;

void *esp_elf_malloc(uint32_t size, bool executable)
{
    (void)executable;
    ++allocation_calls;
    if (allocation_calls == fail_call) {
        return NULL;
    }
    void *pointer = malloc(size);
    assert(pointer != NULL);
    for (size_t i = 0; i < sizeof(allocations) / sizeof(allocations[0]); ++i) {
        if (!allocations[i].live) {
            allocations[i].pointer = pointer;
            allocations[i].live = true;
            return pointer;
        }
    }
    assert(false);
    return NULL;
}

void esp_elf_free(void *pointer)
{
    if (pointer == NULL) {
        return;
    }
    for (size_t i = 0; i < sizeof(allocations) / sizeof(allocations[0]); ++i) {
        if (allocations[i].pointer == pointer) {
            if (!allocations[i].live) {
                ++double_frees;
                return;
            }
            allocations[i].live = false;
            ++frees;
            free(pointer);
            return;
        }
    }
    assert(false);
}

uintptr_t elf_find_sym_default(const char *name)
{
    (void)name;
    return 0;
}

int esp_elf_arch_relocate(esp_elf_t *elf, const elf32_rela_t *rela,
                          const elf32_sym_t *symbol, uint32_t address)
{
    (void)elf;
    (void)rela;
    (void)symbol;
    (void)address;
    return 0;
}

static int live_allocations(void)
{
    int result = 0;
    for (size_t i = 0; i < sizeof(allocations) / sizeof(allocations[0]); ++i) {
        result += allocations[i].live ? 1 : 0;
    }
    return result;
}

static void reset_tracking(int failure)
{
    assert(live_allocations() == 0);
    memset(allocations, 0, sizeof(allocations));
    allocation_calls = 0;
    fail_call = failure;
    frees = 0;
    double_frees = 0;
}

static uint8_t *make_elf(void)
{
    uint8_t *payload = calloc(1, 1024);
    assert(payload != NULL);
    elf32_hdr_t *header = (elf32_hdr_t *)payload;
    header->shoff = sizeof(*header);
    header->shentsize = sizeof(elf32_shdr_t);
    header->shnum = 5;
    header->shstrndx = 1;

    elf32_shdr_t *sections = (elf32_shdr_t *)(payload + header->shoff);
    size_t cursor = header->shoff + header->shnum * sizeof(*sections);
    static const char section_names[] = "\0.shstrtab\0.text\0.dynsym\0.dynstr\0";
    sections[1].type = SHT_STRTAB;
    sections[1].offset = cursor;
    sections[1].size = sizeof(section_names);
    memcpy(payload + cursor, section_names, sizeof(section_names));
    cursor += sizeof(section_names);

    sections[2].name = 11;
    sections[2].type = SHT_PROGBITS;
    sections[2].flags = SHF_ALLOC | SHF_EXECINSTR;
    sections[2].offset = cursor;
    sections[2].size = 4;
    cursor += 4;

    sections[3].name = 17;
    sections[3].type = SHT_SYNSYM;
    sections[3].offset = cursor;
    sections[3].size = 2 * sizeof(elf32_sym_t);
    sections[3].link = 4;
    elf32_sym_t *symbols = (elf32_sym_t *)(payload + cursor);
    symbols[0].name = 1;
    symbols[0].value = 0;
    symbols[0].info = ELF_ST_INFO(STB_GLOBAL, STT_FUNC);
    symbols[1].name = 7;
    symbols[1].value = 1;
    symbols[1].info = ELF_ST_INFO(STB_GLOBAL, STT_FUNC);
    cursor += sections[3].size;

    static const char symbol_names[] = "\0first\0second\0";
    sections[4].name = 25;
    sections[4].type = SHT_STRTAB;
    sections[4].offset = cursor;
    sections[4].size = sizeof(symbol_names);
    memcpy(payload + cursor, symbol_names, sizeof(symbol_names));
    return payload;
}

static void verify_failed_name_allocation(uint8_t *payload, int failed_call,
                                          int completed_names)
{
    reset_tracking(failed_call);
    esp_elf_t elf = {0};
    assert(esp_elf_relocate(&elf, payload) == -ENOMEM);
    assert(elf.num == completed_names);
    assert(elf.symtab != NULL);
    assert(live_allocations() == 2 + completed_names);
    esp_elf_deinit(&elf);
    assert(live_allocations() == 0);
    assert(frees == 2 + completed_names);
    assert(double_frees == 0);
    esp_elf_deinit(&elf);
    assert(frees == 2 + completed_names);
    assert(double_frees == 0);
}

static void verify_success(uint8_t *payload)
{
    reset_tracking(0);
    esp_elf_t elf = {0};
    assert(esp_elf_relocate(&elf, payload) == 0);
    assert(elf.num == 2);
    assert(strcmp(elf.symtab[0].name, "first") == 0);
    assert(strcmp(elf.symtab[1].name, "second") == 0);
    assert(live_allocations() == 4);
    esp_elf_deinit(&elf);
    assert(live_allocations() == 0);
    assert(frees == 4);
    assert(double_frees == 0);
}

int main(void)
{
    uint8_t *payload = make_elf();
    /* ptext=1, symtab=2, first name=3, later name=4. */
    verify_failed_name_allocation(payload, 3, 0);
    verify_success(payload);
    verify_failed_name_allocation(payload, 4, 1);
    verify_success(payload);
    free(payload);
    return 0;
}
'''


class EspElfLoaderPatchTests(unittest.TestCase):
    def _resolved_component(self) -> Path:
        local = ROOT / "firmware/esp32/managed_components/espressif__elf_loader"
        if local.is_dir():
            return local
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        saved = Path(common).parent / "firmware/esp32/managed_components/espressif__elf_loader"
        self.assertTrue(
            saved.is_dir(),
            "resolve pinned firmware components with a PlatformIO firmware build first",
        )
        return saved

    def test_first_and_later_symbol_name_oom_release_every_allocation(self):
        compiler = shutil.which("clang") or shutil.which("gcc")
        self.assertIsNotNone(compiler, "C compiler required for pinned loader check")
        component = self._resolved_component()
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            copied = temp / "managed_components/espressif__elf_loader"
            shutil.copytree(component, copied)
            source = copied / "src/esp_elf.c"
            pristine = source.read_bytes().replace(
                elf_loader_patch.PATCHED_GUARD,
                elf_loader_patch.PRISTINE_GUARD,
                1,
            )
            self.assertEqual(
                elf_loader_patch.PRISTINE_SOURCE_SHA256,
                hashlib.sha256(pristine).hexdigest(),
            )
            source.write_bytes(pristine)
            self.assertTrue(elf_loader_patch.patch_component(temp))
            self.assertFalse(elf_loader_patch.patch_component(temp))

            (temp / "sdkconfig.h").write_text(
                "#define CONFIG_ELF_DYNAMIC_LOAD_SHARED_OBJECT 1\n"
                "#define CONFIG_ELF_LOADER_BUS_ADDRESS_MIRROR 1\n"
            )
            (temp / "soc").mkdir()
            (temp / "soc/soc_caps.h").write_text(
                "#pragma once\n#define SOC_CACHE_INTERNAL_MEM_VIA_L1CACHE 0\n"
            )
            (temp / "esp_log.h").write_text(
                "#pragma once\n"
                "#define ESP_LOGD(...) ((void)0)\n"
                "#define ESP_LOGE(...) ((void)0)\n"
                "#define ESP_LOGI(...) ((void)0)\n"
            )
            harness = temp / "harness.c"
            harness.write_text(HARNESS)
            executable = temp / "elf_cleanup_test"
            compiled = subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-D_GNU_SOURCE",
                    "-DELF_LOADER_VER_MAJOR=1",
                    "-DELF_LOADER_VER_MINOR=3",
                    "-DELF_LOADER_VER_PATCH=2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    *(
                        ["-Wl,-dead_strip"]
                        if sys.platform == "darwin"
                        else ["-Wl,--gc-sections"]
                    ),
                    "-I",
                    str(temp),
                    "-I",
                    str(copied / "include"),
                    str(source),
                    str(harness),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            subprocess.run([str(executable)], check=True)

    def test_rejects_non_pinned_source_and_component_hash(self):
        component = self._resolved_component()
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            copied = temp / "managed_components/espressif__elf_loader"
            shutil.copytree(component, copied)
            source = copied / "src/esp_elf.c"
            pristine = source.read_bytes().replace(
                elf_loader_patch.PATCHED_GUARD,
                elf_loader_patch.PRISTINE_GUARD,
                1,
            )
            source.write_bytes(pristine + b"\n")
            with self.assertRaisesRegex(RuntimeError, "does not match pinned"):
                elf_loader_patch.patch_component(temp)
            source.write_bytes(pristine)
            (copied / ".component_hash").write_text("wrong\n")
            with self.assertRaisesRegex(RuntimeError, "component hash is not pinned"):
                elf_loader_patch.patch_component(temp)
