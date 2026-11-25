#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <tree_sitter/api.h>
#include <unistd.h>

const TSLanguage *tree_sitter_c(void);
const TSLanguage *tree_sitter_cpp(void);

enum { PARSED = 0, FAILED = 1, RECOVERED = 2 };

static char *read_source(const char *path, uint32_t *length) {
    FILE *file = fopen(path, "rb");
    if (!file) {
        perror(path);
        return NULL;
    }
    struct stat info;
    if (fstat(fileno(file), &info) != 0) {
        perror(path);
        fclose(file);
        return NULL;
    }
    if (!S_ISREG(info.st_mode) || info.st_size < 0 ||
        (uintmax_t)info.st_size > UINT32_MAX) {
        fprintf(stderr, "%s: expected a regular file of at most 4 GiB minus one byte\n", path);
        fclose(file);
        return NULL;
    }
    size_t size = (size_t)info.st_size;
    char *source = malloc(size ? size : 1);
    if (!source) {
        perror("malloc");
    } else if (fread(source, 1, size, file) != size || fgetc(file) != EOF || ferror(file)) {
        fprintf(stderr, "%s: could not read entire file or its size changed\n", path);
        free(source);
        source = NULL;
    }
    fclose(file);
    *length = (uint32_t)size;
    return source;
}

int main(int argc, char **argv) {
    if ((argc != 3 && argc != 4) ||
        (strcmp(argv[1], "c") != 0 && strcmp(argv[1], "cpp") != 0) ||
        (argc == 4 && strcmp(argv[3], "--tree") != 0)) {
        fprintf(stderr, "Usage: %s {c|cpp} FILE [--tree]\n", argv[0]);
        return FAILED;
    }
    uint32_t length;
    char *source = read_source(argv[2], &length);
    if (!source)
        return FAILED;

    TSParser *parser = ts_parser_new();
    const TSLanguage *language = strcmp(argv[1], "c") == 0 ? tree_sitter_c() : tree_sitter_cpp();
    if (!ts_parser_set_language(parser, language)) {
        fprintf(stderr, "Tree-sitter rejected the grammar ABI\n");
        ts_parser_delete(parser);
        free(source);
        return FAILED;
    }
    TSTree *tree = ts_parser_parse_string(parser, NULL, source, length);
    int result = FAILED;
    if (tree) {
        TSNode root = ts_tree_root_node(tree);
        result = ts_node_has_error(root) ? RECOVERED : PARSED;
        if (argc == 4) {
            char *expression = ts_node_string(root);
            if (!expression || puts(expression) == EOF || fflush(stdout) != 0)
                result = FAILED;
            free(expression);
        }
        ts_tree_delete(tree);
    } else {
        fprintf(stderr, "%s: Tree-sitter did not return a syntax tree\n", argv[2]);
    }
    ts_parser_delete(parser);
    free(source);
    return result;
}
