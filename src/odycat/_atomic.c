/*
 * odycat._atomic: the 64-bit acquire load and release store that publish
 * output buffers to the engine (see odycat/_shm.py, store_word). Python
 * cannot issue them: a plain store is enough on x86-64, where stores become
 * visible in program order, but ARM and other architectures reorder them, so
 * the group word could be seen before the buffer it publishes.
 *
 * CPython stable ABI (Python 3.11+): one build per architecture.
 *
 * Copyright (c) 2026 Ocean Octane S.L.U.
 * Author: Javier Martínez García
 * SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
 */

#define Py_LIMITED_API 0x030B0000
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>

_Static_assert(__atomic_always_lock_free(sizeof(uint64_t), 0),
               "odycat._atomic needs lock-free 64-bit atomics on this architecture");

/* The aligned 8-byte word at offset off of a buffer, or NULL with an
 * exception set. */
static uint64_t *word_at(Py_buffer *view, Py_ssize_t off) {
    if (off < 0 || off > view->len - 8) {
        PyErr_Format(PyExc_ValueError, "offset %zd outside a %zd-byte buffer", off, view->len);
        return NULL;
    }
    char *p = (char *)view->buf + off;
    if ((uintptr_t)p % 8) {
        PyErr_Format(PyExc_ValueError, "offset %zd is not 8-byte aligned", off);
        return NULL;
    }
    return (uint64_t *)p;
}

static PyObject *load_acquire_u64(PyObject *self, PyObject *args) {
    PyObject *obj;
    Py_ssize_t off;
    Py_buffer view;
    (void)self;
    if (!PyArg_ParseTuple(args, "On", &obj, &off))
        return NULL;
    if (PyObject_GetBuffer(obj, &view, PyBUF_SIMPLE) < 0)
        return NULL;
    uint64_t *w = word_at(&view, off);
    PyObject *result = NULL;
    if (w)
        result = PyLong_FromUnsignedLongLong(__atomic_load_n(w, __ATOMIC_ACQUIRE));
    PyBuffer_Release(&view);
    return result;
}

static PyObject *store_release_u64(PyObject *self, PyObject *args) {
    PyObject *obj, *value_obj;
    Py_ssize_t off;
    Py_buffer view;
    (void)self;
    if (!PyArg_ParseTuple(args, "OnO", &obj, &off, &value_obj))
        return NULL;
    unsigned long long value = PyLong_AsUnsignedLongLong(value_obj);
    if (value == (unsigned long long)-1 && PyErr_Occurred())
        return NULL;
    if (PyObject_GetBuffer(obj, &view, PyBUF_WRITABLE) < 0)
        return NULL;
    uint64_t *w = word_at(&view, off);
    if (w)
        __atomic_store_n(w, (uint64_t)value, __ATOMIC_RELEASE);
    PyBuffer_Release(&view);
    if (!w)
        return NULL;
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"load_acquire_u64", load_acquire_u64, METH_VARARGS,
     "load_acquire_u64(buffer, offset) -> int: 64-bit acquire load of an aligned word."},
    {"store_release_u64", store_release_u64, METH_VARARGS,
     "store_release_u64(buffer, offset, value): 64-bit release store of an aligned word."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "odycat._atomic",
    "64-bit acquire load and release store on shared memory (see odycat._shm).",
    -1, methods, NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC PyInit__atomic(void) {
    return PyModule_Create(&module);
}
