cmake_minimum_required(VERSION 3.16)
project(Doris-UT VERSION 6.0.0 LANGUAGES C CXX)

# -----------------------------------------------------------------------
# Global settings
# -----------------------------------------------------------------------
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Generates build/compile_commands.json → required for clang-tidy (Phase 7)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Default build type
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING
      "Build type: Debug | Release | ASan" FORCE)
endif()
message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")

# -----------------------------------------------------------------------
# ASan / UBSan build type
# -----------------------------------------------------------------------
set(CMAKE_CXX_FLAGS_ASAN
    "-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer"
    CACHE STRING "Flags for ASan build" FORCE)
set(CMAKE_C_FLAGS_ASAN
    "-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer"
    CACHE STRING "" FORCE)
set(CMAKE_EXE_LINKER_FLAGS_ASAN
    "-fsanitize=address,undefined"
    CACHE STRING "" FORCE)

# -----------------------------------------------------------------------
# Find dependencies
# -----------------------------------------------------------------------

# FFTW3 single-precision (fftw3f)
find_path(FFTW3_INCLUDE_DIR
    NAMES fftw3.h
    HINTS /usr/local/include /usr/include)
find_library(FFTW3F_LIBRARY
    NAMES fftw3f
    HINTS /usr/local/lib /usr/lib)
if(NOT FFTW3_INCLUDE_DIR OR NOT FFTW3F_LIBRARY)
  message(FATAL_ERROR
    "fftw3f not found.\n"
    "  FFTW3_INCLUDE_DIR=${FFTW3_INCLUDE_DIR}\n"
    "  FFTW3F_LIBRARY=${FFTW3F_LIBRARY}\n"
    "Install with: apt-get install libfftw3-dev  OR  build from source to /usr/local")
endif()
message(STATUS "FFTW3 include: ${FFTW3_INCLUDE_DIR}")
message(STATUS "FFTW3 library: ${FFTW3F_LIBRARY}")

# LAPACK (optional — off by default to match Makefile baseline; enable with -DDORIS_USE_LAPACK=ON)
option(DORIS_USE_LAPACK "Use LAPACK for Cholesky and matrix decompositions" OFF)
if(DORIS_USE_LAPACK)
  find_package(LAPACK REQUIRED)
  message(STATUS "LAPACK: ${LAPACK_LIBRARIES}")
else()
  message(STATUS "LAPACK: disabled (Makefile-compatible baseline)")
endif()

# -----------------------------------------------------------------------
# triangle.c — external Shewchuk code, compiled as C only
# -----------------------------------------------------------------------
# triangle.c is compiled as C++ (to match Makefile: CC=g++ compiles .c as C++)
# so that name mangling of triangulate() matches the call sites in referencephase.cc.
set_source_files_properties(triangle.c PROPERTIES LANGUAGE CXX)
add_library(triangle_obj OBJECT triangle.c)
target_compile_definitions(triangle_obj PRIVATE TRILIBRARY ANSI_DECLARATORS)
# Disable clang-tidy for this external Shewchuk file
set_target_properties(triangle_obj PROPERTIES CXX_CLANG_TIDY "")

# -----------------------------------------------------------------------
# Doris sources
# -----------------------------------------------------------------------
# These are compiled as C++ (template instantiation via matrixspecs.cc includes matrixbk.cc)
set(DORIS_SRCS
    processor.cc
    utilities.cc
    ioroutines.cc
    readinput.cc
    readdata.cc
    coregistration.cc
    filtering.cc
    referencephase.cc
    products.cc
    geocode.cc
    unwrap.cc
    matrixspecs.cc
    exceptions.cc
    estorbit.cc
    # These contain class templates / inline definitions compiled per-TU
    tmp_strptime.cc
    slcimage.cc
    productinfo.cc
    orbitbk.cc
)

add_executable(doris ${DORIS_SRCS} $<TARGET_OBJECTS:triangle_obj>)

# -----------------------------------------------------------------------
# Compile options
# -----------------------------------------------------------------------
target_compile_features(doris PRIVATE cxx_std_17)

target_compile_definitions(doris PRIVATE
    __USE_FFTW_LIBRARY__
    __X86PROCESSOR__
    _LARGEFILE64_SOURCE
    _FILE_OFFSET_BITS=64
    _LARGEFILE_SOURCE
)

if(DORIS_USE_LAPACK)
  target_compile_definitions(doris PRIVATE __USE_LAPACK_LIBRARY__)
endif()

target_include_directories(doris PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${FFTW3_INCLUDE_DIR}
)

# -----------------------------------------------------------------------
# Link
# -----------------------------------------------------------------------
target_link_libraries(doris PRIVATE
    ${FFTW3F_LIBRARY}
    m
)
if(DORIS_USE_LAPACK)
  target_link_libraries(doris PRIVATE ${LAPACK_LIBRARIES})
endif()

# -----------------------------------------------------------------------
# Warnings
# -----------------------------------------------------------------------
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(doris PRIVATE
      -Wall
      -Wextra
      -Wuninitialized
      -Wno-unused-parameter     # too noisy in legacy code
      -Wno-sign-compare         # too noisy in legacy code
  )
  if(CMAKE_BUILD_TYPE STREQUAL "Release")
    target_compile_options(doris PRIVATE -O2)
  elseif(CMAKE_BUILD_TYPE STREQUAL "Debug")
    target_compile_options(doris PRIVATE -O0 -g)
  endif()
endif()

# -----------------------------------------------------------------------
# Install
# -----------------------------------------------------------------------
install(TARGETS doris DESTINATION bin)
