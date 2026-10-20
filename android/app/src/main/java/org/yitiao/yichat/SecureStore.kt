package org.yitiao.yichat

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Keystore wraps random account keys. Ciphertexts are bound to their row/name. */
class SecureStore(private val context: Context) {
    private val random = SecureRandom()
    private val directory = File(context.noBackupFilesDir, "vault").apply { mkdirs() }
    private val master: SecretKey by lazy {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("yichat.wrap.v1", null) as? SecretKey) ?: KeyGenerator.getInstance("AES", "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("yichat.wrap.v1", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).setRandomizedEncryptionRequired(true).build())
        }.generateKey()
    }
    @Synchronized fun put(name: String, bytes: ByteArray) {
        require(name.matches(Regex("[A-Za-z0-9_.-]+")))
        val f = AtomicFile(File(directory, name))
        val stream = f.startWrite()
        try { stream.write(seal(master, name, bytes)); f.finishWrite(stream) }
        catch (e: Exception) { f.failWrite(stream); throw e }
    }
    @Synchronized fun get(name: String): ByteArray? {
        val f = File(directory, name)
        return if (f.exists() || File(f.path+".bak").exists()) open(master, name, AtomicFile(f).readFully()) else null
    }
    @Synchronized fun delete(name: String) = AtomicFile(File(directory, name)).delete()
    @Synchronized fun accountKey(account: String, purpose: String): ByteArray {
        val name = "$account.$purpose"
        return get(name) ?: ByteArray(32).also { random.nextBytes(it); put(name, it) }
    }
    fun encrypt(account: String, row: String, data: ByteArray) = seal(SecretKeySpec(accountKey(account, "data"), "AES"), row, data)
    fun decrypt(account: String, row: String, data: ByteArray) = open(SecretKeySpec(accountKey(account, "data"), "AES"), row, data)
    companion object {
        fun hash(s: String) = java.security.MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }
        fun seal(key: SecretKey, aad: String, data: ByteArray): ByteArray {
            val c = Cipher.getInstance("AES/GCM/NoPadding"); c.init(Cipher.ENCRYPT_MODE, key); c.updateAAD(aad.toByteArray())
            return byteArrayOf(1) + c.iv + c.doFinal(data)
        }
        fun open(key: SecretKey, aad: String, data: ByteArray): ByteArray {
            require(data.size >= 29 && data[0] == 1.toByte())
            val c = Cipher.getInstance("AES/GCM/NoPadding"); c.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, data.copyOfRange(1, 13)))
            c.updateAAD(aad.toByteArray()); return c.doFinal(data.copyOfRange(13, data.size))
        }
    }
}
