#include "skiplist.c"

volatile int pypy_codemap_currently_invalid = 0;

void pypy_codemap_invalid_set(int value)
{
    if (value)
        __sync_lock_test_and_set(&pypy_codemap_currently_invalid, 1);
    else
        __sync_lock_release(&pypy_codemap_currently_invalid);
}


/************************************************************/
/***  codemap storage                                     ***/
/************************************************************/

typedef struct {
    unsigned int machine_code_size;
    unsigned int bytecode_info_size;
    long *bytecode_info;
} codemap_data_t;

static skipnode_t jit_codemap_head;

/*** interface used from codemap.py ***/

long pypy_jit_codemap_add(uintptr_t addr, unsigned int machine_code_size,
                          long *bytecode_info, unsigned int bytecode_info_size)
{
    skipnode_t *new = skiplist_malloc(sizeof(codemap_data_t));
    codemap_data_t *data;
    if (new == NULL)
        return -1;   /* too bad */

    new->key = addr;
    data = (codemap_data_t *)new->data;
    data->machine_code_size = machine_code_size;
    data->bytecode_info = bytecode_info;
    data->bytecode_info_size = bytecode_info_size;

    pypy_codemap_invalid_set(1);
    skiplist_insert(&jit_codemap_head, new);
    pypy_codemap_invalid_set(0);
    return 0;
}

void pypy_jit_codemap_del(uintptr_t addr)
{
    pypy_codemap_invalid_set(1);
    skiplist_remove(&jit_codemap_head, addr);
    pypy_codemap_invalid_set(0);
}

/*** interface used from pypy/module/_vmprof ***/

void *pypy_find_codemap_at_addr(long addr)
{
    skiplist_t *codemap = skiplist_search(&jit_codemap_head, addr);
    codemap_data_t *data;
    uintptr_t rel_addr;

    if (codemap == &jit_codemap_head)
        return NULL;

    rel_addr = (uintptr_t)addr - codemap->key;
    data = (codemap_data_t *)codemap->data;
    if (rel_addr >= data->machine_code_size)
        return NULL;

    return (void *)codemap;
}

long pypy_yield_codemap_at_addr(void *codemap_raw, long addr,
                                long *current_pos_addr)
{
    // will return consecutive unique_ids from codemap, starting from position
    // `pos` until addr
    skiplist_t *codemap = (skiplist_t *)codemap_raw;
    long current_pos = *current_pos_addr;
    long rel_addr = addr - codemap->key;
    long next_start, next_stop;
    codemap_data_t *data = (codemap_data_t *)codemap->data;

    while (1) {
        if (current_pos >= data->bytecode_info_size)
            return 0;
        next_start = data->bytecode_info[current_pos + 1];
        if (next_start > rel_addr)
            return 0;
        next_stop = data->bytecode_info[current_pos + 2];
        if (next_stop > rel_addr) {
            *current_pos_addr = current_pos + 4;
            return data->bytecode_info[current_pos];
        }
        // we need to skip potentially more than one
        current_pos = data->bytecode_info[current_pos + 3];
    }
}

/************************************************************/
/***  depthmap storage                                    ***/
/************************************************************/

typedef struct {
    unsigned int block_size;
    unsigned int stack_depth;
} depthmap_data_t;

static skipnode_t jit_depthmap_head;

/*** interface used from codemap.py ***/

long pypy_jit_depthmap_add(uintptr_t addr, unsigned int size,
                           unsigned int stackdepth)
{
    skipnode_t *new = skiplist_malloc(sizeof(depthmap_data_t));
    depthmap_data_t *data;
    if (new == NULL)
        return -1;   /* too bad */

    new->key = addr;
    data = (depthmap_data_t *)new->data;
    data->block_size = size;
    data->stack_depth = stackdepth;

    pypy_codemap_invalid_set(1);
    skiplist_insert(&jit_depthmap_head, new);
    pypy_codemap_invalid_set(0);
    return 0;
}

void pypy_jit_depthmap_clear(uintptr_t addr, unsigned int size)
{
    uintptr_t search_key = addr + size - 1;
    if (size == 0)
        return;

    pypy_codemap_invalid_set(1);
    while (1) {
        /* search for all nodes belonging to the range, and remove them */
        skipnode_t *node = skiplist_search(&jit_depthmap_head, search_key);
        if (node->addr < addr)
            break;   /* exhausted */
        skiplist_remove(&jit_depthmap_head, node->addr);
    }
    pypy_codemap_invalid_set(0);
}

/*** interface used from pypy/module/_vmprof ***/

long pypy_jit_stack_depth_at_loc(long loc)
{
    skiplist_t *depthmap = skiplist_search(&jit_depthmap_head, (uintptr_t)loc);
    depthmap_data_t *data;
    uintptr_t rel_addr;

    if (depthmap == &jit_depthmap_head)
        return -1;

    rel_addr = (uintptr_t)loc - depthmap->key;
    data = (codemap_data_t *)depthmap->data;
    if (rel_addr >= data->block_size)
        return -1;

    return data->stack_depth;
}

/************************************************************/
