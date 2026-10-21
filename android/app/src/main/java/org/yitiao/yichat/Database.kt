package org.yitiao.yichat

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Entity data class Operation(@PrimaryKey val key: String, val kind: String, val state: String, val encrypted: ByteArray, val updated: Long)
@Entity(primaryKeys = ["room", "event"]) data class Packet(val room: String, val event: String, val sequence: Long, val hash: String, val encrypted: ByteArray)
@Entity(primaryKeys = ["room", "event"]) data class Tombstone(val room: String, val event: String, val sequence: Long)
@Entity(primaryKeys = ["room", "event"]) data class TrustedReceipt(val room: String, val event: String, val hash: String, val sequence: Long, val encrypted: ByteArray)
@Entity data class Watermark(@PrimaryKey val room: String, val untilMs: Long, val cursor: String?)
@Dao interface LocalDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun saveOperation(value: Operation)
    @Query("SELECT * FROM Operation ORDER BY updated DESC") fun operations(): Flow<List<Operation>>
    @Query("SELECT * FROM Operation WHERE `key`=:key") suspend fun operation(key: String): Operation?
    @Query("SELECT * FROM Operation") suspend fun allOperations(): List<Operation>
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun addPacket(value: Packet)
    @Query("SELECT * FROM Packet WHERE room=:room AND event=:event") suspend fun packet(room: String, event: String): Packet?
    @Query("SELECT * FROM Packet") suspend fun packets(): List<Packet>
    @Query("SELECT COUNT(*) FROM Packet") suspend fun packetCount(): Long
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun tombstone(value: Tombstone)
    @Query("SELECT * FROM Tombstone WHERE room=:room AND event=:event") suspend fun tombstone(room: String, event: String): Tombstone?
    @Query("SELECT * FROM Tombstone") suspend fun tombstones(): List<Tombstone>
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun watermark(value: Watermark)
    @Query("SELECT * FROM Watermark WHERE room=:room") suspend fun watermark(room: String): Watermark?
    @Query("SELECT * FROM Watermark") suspend fun watermarks(): List<Watermark>
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun receipt(value: TrustedReceipt)
    @Query("SELECT * FROM TrustedReceipt WHERE room=:room AND event=:event") suspend fun receipt(room: String, event: String): TrustedReceipt?
    @Query("SELECT DISTINCT room FROM TrustedReceipt ORDER BY room") suspend fun recordRooms(): List<String>
    @Query("SELECT * FROM TrustedReceipt WHERE room=:room AND sequence>:after ORDER BY sequence LIMIT :limit") suspend fun recordReceipts(room: String, after: Long, limit: Int): List<TrustedReceipt>
    @Query("SELECT * FROM TrustedReceipt") suspend fun receipts(): List<TrustedReceipt>
    @Query("DELETE FROM Packet WHERE room=:room AND event=:event") suspend fun deletePacket(room: String, event: String)
}
@Database(entities = [Operation::class, Packet::class, Tombstone::class, TrustedReceipt::class, Watermark::class], version = 1, exportSchema = true)
abstract class YiChatDatabase : RoomDatabase() { abstract fun local(): LocalDao }
