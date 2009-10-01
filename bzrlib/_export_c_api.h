/* Copyright (C) 2009 Canonical Ltd
 * 
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
 */


/* This file contains helper functions for exporting a C API for a CPython
 * extension module.
 */

#ifndef _EXPORT_C_API_H_
#define _EXPORT_C_API_H_

static const char *_C_API_NAME = "_C_API";

/**
 * Add a C function to the modules _C_API
 * This wraps the function in a PyCObject, and inserts that into a dict.
 * The key of the dict is the function name, and the description is the
 * signature of the function.
 * This is generally called during a modules init_MODULE function.
 *
 * @param   module  A Python module (the one being initialized)
 * @param   funcname The name of the function being exported
 * @param   func    A pointer to the function
 * @param   signature The C signature of the function
 * @return  0 if everything is successful, -1 if there is a problem. An
 *          exception should also be set
 */
static int
_export_function(PyObject *module, char *funcname, void *func, char *signature)
{
    PyObject *d = NULL;
    PyObject *c_obj = NULL;

    d = PyObject_GetAttrString(module, _C_API_NAME);
    if (!d) {
        PyErr_Clear();
        d = PyDict_New();
        if (!d)
            goto bad;
        Py_INCREF(d);
        if (PyModule_AddObject(module, _C_API_NAME, d) < 0)
            goto bad;
    }
    c_obj = PyCObject_FromVoidPtrAndDesc(func, signature, 0);
    if (!c_obj)
        goto bad;
    if (PyDict_SetItemString(d, funcname, c_obj) < 0)
        goto bad;
    Py_DECREF(d);
    return 0;
bad:
    Py_XDECREF(c_obj);
    Py_XDECREF(d);
    return -1;
}

#endif // _EXPORT_C_API_H_
