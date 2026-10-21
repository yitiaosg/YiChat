package org.matrix.rustsdk

import android.content.Context
import androidx.annotation.Keep

/** Exact JNI entrypoint exported by the official Matrix Rust SDK. */
@Keep
object Android {
    init { System.loadLibrary("matrix_sdk_ffi") }
    @JvmStatic external fun init(context: Context)
}
