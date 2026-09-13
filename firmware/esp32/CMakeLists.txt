cmake_minimum_required(VERSION 3.16.0)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(ledgrid_receiver)

# project() resolves managed components. Patch before any source compilation;
# a buildprog pre-action runs only after its firmware dependency is built.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
             "${CMAKE_CURRENT_LIST_DIR}/elf_loader_patch.py")
idf_build_get_property(elf_patch_python PYTHON)
execute_process(
  COMMAND "${elf_patch_python}" "${CMAKE_CURRENT_LIST_DIR}/elf_loader_patch.py"
          "${CMAKE_CURRENT_LIST_DIR}"
  RESULT_VARIABLE elf_patch_result)
if(NOT elf_patch_result EQUAL 0)
  message(FATAL_ERROR "Pinned elf_loader cleanup patch failed")
endif()
