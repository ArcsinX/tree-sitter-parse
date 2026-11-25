cmake_minimum_required(VERSION 3.18)
project(tree_sitter_parse LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

function(unpack_crate name version sha256 marker output)
    set(package "${name}-${version}")
    set(destination "${CMAKE_BINARY_DIR}/deps")
    set(source "${destination}/${package}")
    if(NOT EXISTS "${source}/${marker}")
        set(cargo_home "$ENV{CARGO_HOME}")
        if(cargo_home STREQUAL "")
            set(cargo_home "$ENV{HOME}/.cargo")
        endif()
        file(GLOB cached "${cargo_home}/registry/cache/*/${package}.crate")
        if(cached)
            list(GET cached 0 archive)
            file(SHA256 "${archive}" actual_sha256)
            if(NOT actual_sha256 STREQUAL sha256)
                message(FATAL_ERROR "Checksum mismatch for ${archive}")
            endif()
        else()
            set(archive "${destination}/${package}.crate")
            file(MAKE_DIRECTORY "${destination}")
            file(DOWNLOAD "https://static.crates.io/crates/${name}/${package}.crate"
                "${archive}" EXPECTED_HASH "SHA256=${sha256}" TLS_VERIFY ON)
        endif()
        file(ARCHIVE_EXTRACT INPUT "${archive}" DESTINATION "${destination}")
    endif()
    set(${output} "${source}" PARENT_SCOPE)
endfunction()

unpack_crate(tree-sitter 0.25.10
    78f873475d258561b06f1c595d93308a7ed124d9977cb26b148c2084a4a3cc87 src/lib.c runtime)
unpack_crate(tree-sitter-c 0.24.2
    a9b2eb57a55fed6b00812912e730b7a275cf4fe98bfd6a5d76263d4438371728 src/parser.c c_grammar)
unpack_crate(tree-sitter-cpp 0.23.4
    df2196ea9d47b4ab4a31b9297eaa5a5d19a0b121dceb9f118f6790ad0ab94743 src/parser.c cpp_grammar)

add_library(ts_runtime STATIC "${runtime}/src/lib.c")
target_include_directories(ts_runtime PUBLIC "${runtime}/include")
target_compile_definitions(ts_runtime PRIVATE _DEFAULT_SOURCE _DARWIN_C_SOURCE)

add_library(ts_c STATIC "${c_grammar}/src/parser.c")
target_include_directories(ts_c PRIVATE "${c_grammar}/src")

add_library(ts_cpp STATIC "${cpp_grammar}/src/parser.c" "${cpp_grammar}/src/scanner.c")
target_include_directories(ts_cpp PRIVATE "${cpp_grammar}/src")

add_executable(ts_parse parser.c)
target_link_libraries(ts_parse PRIVATE ts_runtime ts_c ts_cpp)
