/*
 * delta.h: headers for delta functionality
 *
 * Adapted from GIT for Bazaar by
 *   John Arbash Meinel <john@arbash-meinel.com> (C) 2009
 *
 * This code is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 */
#ifndef DELTA_H
#define DELTA_H

/* opaque object for delta index */
struct delta_index;

struct source_info {
    const void *buf; /* Pointer to the beginning of source data */
    unsigned long size; /* Total length of source data */
    unsigned long agg_offset; /* Start of source data as part of the
                                 aggregate source */
};

/* result type for functions that have multiple failure modes */
typedef enum {
    DELTA_OK,             /* Success */
    DELTA_OUT_OF_MEMORY,  /* Could not allocate required memory */
    DELTA_INDEX_NEEDED,   /* A delta_index must be passed */
    DELTA_SOURCE_EMPTY,   /* A source_info had no content */
    DELTA_SOURCE_BAD,     /* A source_info had invalid or corrupt content */
    DELTA_BUFFER_EMPTY,   /* A buffer pointer and size */
    DELTA_SIZE_TOO_BIG,   /* Delta data is larger than the max requested */
} delta_result;


/*
 * create_delta_index: compute index data from given buffer
 *
 * Returns a delta_result status, when DELTA_OK then *fresh is set to a struct
 * delta_index that should be passed to subsequent create_delta() calls, or to
 * free_delta_index().  Other values are a failure, and *fresh is unset.
 * The given buffer must not be freed nor altered before free_delta_index() is
 * called. The resultant struct must be freed using free_delta_index().
 *
 * :param max_bytes_to_index: Limit the number of regions to sample to this
 *      amount of text. We will store at most max_bytes_to_index / RABIN_WINDOW
 *      pointers into the source text.  Useful if src can be unbounded in size,
 *      and you are willing to trade match accuracy for peak memory.
 */
extern delta_result
create_delta_index(const struct source_info *src,
                   struct delta_index *old,
                   struct delta_index **fresh,
                   int max_bytes_to_index);


/*
 * create_delta_index_from_delta: compute index data from given buffer
 *
 * Returns a delta_result status, when DELTA_OK then *fresh is set to a struct
 * delta_index that should be passed to subsequent create_delta() calls, or to
 * free_delta_index().  Other values are a failure, and *fresh is unset.
 * The bytes must be in the form of a delta structure, as generated by
 * create_delta(). The generated index will only index the insert bytes, and
 * not any of the control structures.
 */
extern delta_result
create_delta_index_from_delta(const struct source_info *delta,
                              struct delta_index *old,
                              struct delta_index **fresh);
/*
 * free_delta_index: free the index created by create_delta_index()
 *
 * Given pointer must be what create_delta_index() returned, or NULL.
 */
extern void free_delta_index(struct delta_index *index);

/*
 * sizeof_delta_index: returns memory usage of delta index
 *
 * Given pointer must be what create_delta_index() returned, or NULL.
 */
extern unsigned long sizeof_delta_index(struct delta_index *index);

/*
 * create_delta: create a delta from given index for the given buffer
 *
 * This function may be called multiple times with different buffers using
 * the same delta_index pointer.  If max_delta_size is non-zero and the
 * resulting delta is to be larger than max_delta_size then DELTA_SIZE_TOO_BIG
 * is returned.  Otherwise on success, DELTA_OK is returned and *delta_data is
 * set to a new buffer with the delta data and *delta_size is updated with its
 * size.  That buffer must be freed by the caller.
 */
extern delta_result
create_delta(const struct delta_index *index,
             const void *buf, unsigned long bufsize,
             unsigned long *delta_size, unsigned long max_delta_size,
             void **delta_data);

/* the smallest possible delta size is 3 bytes
 * Target size, Copy command, Copy length
 */
#define DELTA_SIZE_MIN  3

/*
 * This must be called twice on the delta data buffer, first to get the
 * expected source buffer size, and again to get the target buffer size.
 */
static unsigned long
get_delta_hdr_size(unsigned char **datap, const unsigned char *top)
{
    unsigned char *data = *datap;
    unsigned char cmd;
    unsigned long size = 0;
    int i = 0;
    do {
        cmd = *data++;
        size |= (cmd & ~0x80) << i;
        i += 7;
    } while (cmd & 0x80 && data < top);
    *datap = data;
    return size;
}

/*
 * Return the basic information about a given delta index.
 * :param index: The delta_index object
 * :param pos: The offset in the entry list. Start at 0, and walk until you get
 *      0 as a return code.
 * :param global_offset: return value, distance to the beginning of all sources
 * :param hash_val: return value, the RABIN hash associated with this pointer
 * :param hash_offset: Location for this entry in the hash array.
 * :return: 1 if pos != -1 (there was data produced)
 */
extern int
get_entry_summary(const struct delta_index *index, int pos,
                  unsigned int *text_offset, unsigned int *hash_val);

/*
 * Determine what entry index->hash[X] points to.
 */
extern int
get_hash_offset(const struct delta_index *index, int pos,
                unsigned int *entry_offset);

/*
 * Compute the rabin_hash of the given data, it is assumed the data is at least
 * RABIN_WINDOW wide (16 bytes).
 */
extern unsigned int
rabin_hash(const unsigned char *data);

#endif
