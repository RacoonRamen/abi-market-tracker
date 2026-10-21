package com.keyslave.abi

import android.Manifest
import android.app.*
import android.content.BroadcastReceiver
import android.content.IntentFilter
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.net.Uri
import android.app.DownloadManager
import android.os.*
import android.os.Environment
import android.provider.Settings
import android.view.Gravity
import android.view.WindowManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.*

private const val REPO = "RacoonRamen/abi-market-tracker"
private const val MARKET_URL = "https://abi-tracker.azurewebsites.net/Market/View?minorId=40505"
private const val PREFS = "abi"
private const val CHANNEL = "abi_price"

private val EXTRACT_JS = """
(function(){
  var cards=document.querySelectorAll('#marketItemGrid .market-item-card');
  var out=[];
  for(var i=0;i<cards.length;i++){
    var c=cards[i], n=c.querySelector('.market-item-name');
    var v=c.querySelector('.market-item-lowest-value');
    var q=c.querySelector('.market-item-count') || c.querySelector('.market-item-total-count') || c.querySelector('.market-item-stock');
    var t=v?v.textContent.trim():'';
    if(!/\d/.test(t)) continue;
    out.push({id:Number(c.dataset.itemId),
              name:n?(n.getAttribute('title')||n.textContent.trim()):'',
              low:t, count:(q&&q.textContent.trim())?q.textContent.trim():((c.textContent||'').match(/在售\s*([\d,]+)/)||[])[1]||''});
  }
  return JSON.stringify(out);
})()
"""

object Scrape {
    fun hasOverlay(ctx: Context) = Settings.canDrawOverlays(ctx)

    private fun pat(ctx: Context) =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("pat", "")?.trim().orEmpty()

    private fun api(pat: String, path: String): HttpURLConnection {
        val c = URL("https://api.github.com/repos/$REPO/$path").openConnection() as HttpURLConnection
        c.setRequestProperty("Authorization", "Bearer $pat")
        c.setRequestProperty("Accept", "application/vnd.github+json")
        c.setRequestProperty("User-Agent", "abi-price-app")
        c.connectTimeout = 15000
        c.readTimeout = 15000
        return c
    }

    /** 讀 scrape_request.json，回 (id, minor) */
    fun fetchRequest(ctx: Context): Pair<Long, String>? {
        val p = pat(ctx)
        if (p.isEmpty()) return null
        val c = api(p, "contents/scrape_request.json").apply {
            setRequestProperty("Accept", "application/vnd.github.raw")
        }
        return try {
            if (c.responseCode == 200) {
                val t = BufferedReader(InputStreamReader(c.inputStream)).readText()
                val o = JSONObject(t)
                val id = o.optLong("id", 0L)
                if (id <= 0) null else id to o.optString("minor", "40505")
            } else null
        } catch (_: Exception) {
            null
        } finally {
            c.disconnect()
        }
    }

    /** 依序抓多個分類，最後一次上傳 */
    fun run(ctx: Context, minors: List<String>, reqId: Long, cb: (String) -> Unit) {
        if (!hasOverlay(ctx)) { cb("缺少「顯示在其他應用程式上層」權限"); return }
        val all = JSONArray()
        fun step(i: Int) {
            if (i >= minors.size) {
                if (all.length() == 0) { cb("沒有解析到任何價格"); return }
                upload(ctx, all, reqId, minors.joinToString(",")) { msg -> cb(msg) }
                return
            }
            scrapeOne(ctx, minors[i]) { arr ->
                if (arr == null) {
                    cb("逾時：${minors[i]} 沒出現價格")
                } else {
                    for (k in 0 until arr.length()) all.put(arr.getJSONObject(k))
                    step(i + 1)
                }
            }
        }
        step(0)
    }

    /** 抓單一分類（1px 隱藏 WebView） */
    private fun scrapeOne(ctx: Context, minor: String, cb: (JSONArray?) -> Unit) {
        val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val web = WebView(ctx)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.userAgentString =
            "Mozilla/5.0 (Linux; Android 14; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        val lp = WindowManager.LayoutParams(
            1, 1, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT
        ).apply { gravity = Gravity.TOP or Gravity.START; alpha = 0.01f }
        try { wm.addView(web, lp) } catch (e: Exception) { cb(null); return }

        var done = false
        var tries = 0
        val handler = Handler(Looper.getMainLooper())
        lateinit var poll: () -> Unit

        fun finish(arr: JSONArray?) {
            if (done) return
            done = true
            handler.removeCallbacksAndMessages(null)
            try { web.destroy(); wm.removeView(web) } catch (_: Exception) {}
            cb(arr)
        }

        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) { poll() }
        }

        val merged = JSONObject()
        var prevSig = ""
        var stable = 0

        fun mergeInto(arr: JSONArray) {
            for (k in 0 until arr.length()) {
                val o = arr.optJSONObject(k) ?: continue
                val id = o.optString("id")
                if (id.isEmpty()) continue
                val low = o.optString("low").filter { it.isDigit() }.toLongOrNull() ?: 0L
                if (low <= 0L) continue
                val oldLow = merged.optJSONObject(id)?.optString("low")
                    ?.filter { it.isDigit() }?.toLongOrNull() ?: Long.MAX_VALUE
                if (low < oldLow) merged.put(id, o)
            }
        }

        fun mergedArray(): JSONArray {
            val out = JSONArray()
            val keys = merged.keys()
            while (keys.hasNext()) out.put(merged.getJSONObject(keys.next()))
            return out
        }

        fun signature(): String {
            val ids = ArrayList<String>()
            val keys = merged.keys()
            while (keys.hasNext()) ids.add(keys.next())
            ids.sort()
            val sb = StringBuilder()
            for (id in ids) {
                sb.append(id).append(':').append(merged.getJSONObject(id).optString("low")).append(';')
            }
            return sb.toString()
        }

        fun startPoll() {
            if (done) return
            tries++
            if (tries > 45) {
                finish(if (merged.length() > 0) mergedArray() else null)
                return
            }
            web.evaluateJavascript(EXTRACT_JS) { raw ->
                if (done) return@evaluateJavascript
                val arr = parse(raw)
                if (arr != null) mergeInto(arr)
                val sig = signature()
                if (merged.length() > 0 && sig == prevSig) stable++ else stable = 0
                prevSig = sig
                if (merged.length() > 0 && stable >= 2) finish(mergedArray())
                else handler.postDelayed({ startPoll() }, 900)
            }
        }

        poll = { startPoll() }
        handler.postDelayed({
            web.loadUrl("https://abi-tracker.azurewebsites.net/Market/View?minorId=$minor")
        }, 250)
        handler.postDelayed({ if (!done) finish(null) }, 60_000)
    }

    private fun parse(raw: String?): JSONArray? {
        if (raw == null || raw == "null") return null
        return try {
            val s = JSONTokener(raw).nextValue() as? String ?: return null
            val a = JSONArray(s)
            if (a.length() == 0) null else a
        } catch (e: Exception) { null }
    }

    private fun digits(s: String?): Long =
        (s ?: "").filter { it.isDigit() }.toLongOrNull() ?: 0L

    private fun upload(ctx: Context, arr: JSONArray, reqId: Long, minorLabel: String, cb: (String) -> Unit) {
        val p = pat(ctx)
        if (p.isEmpty()) { cb("缺少 GitHub PAT"); return }

        Thread {
            val items = JSONArray()
            for (k in 0 until arr.length()) {
                val o = arr.getJSONObject(k)
                val low = digits(o.optString("low"))
                if (low <= 0) continue
                items.put(JSONObject().apply {
                    put("id", o.optLong("id"))
                    put("name", o.optString("name"))
                    put("low", low)
                    put("count", digits(o.optString("count")))
                })
            }
            if (items.length() == 0) { cb("沒有解析到任何價格"); return@Thread }

            val ts = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
                .apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date())
            val payload = JSONObject().apply {
                put("fetched_at", ts)
                put("minor", minorLabel)
                put("source", "android-app")
                put("req", reqId)
                put("items", items)
            }
            val msg = try {
                putFile(p, "phone_prices.json", payload.toString(1), "phone prices $ts (req $reqId)")
            } catch (e: Exception) {
                "上傳失敗: ${e.message}"
            }
            Handler(Looper.getMainLooper()).post { cb(msg) }
        }.start()
    }

    private fun putFile(pat: String, name: String, content: String, message: String): String {
        var sha: String? = null
        val g = api(pat, "contents/$name")
        if (g.responseCode == 200) {
            val body = BufferedReader(InputStreamReader(g.inputStream)).readText()
            sha = JSONObject(body).optString("sha").ifEmpty { null }
        }
        g.disconnect()

        val body = JSONObject().apply {
            put("message", message)
            put("content", android.util.Base64.encodeToString(
                content.toByteArray(), android.util.Base64.NO_WRAP))
            if (sha != null) put("sha", sha)
        }.toString()

        val p = api(pat, "contents/$name").apply {
            requestMethod = "PUT"
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
        }
        p.outputStream.use { os: OutputStream -> os.write(body.toByteArray()) }
        val code = p.responseCode
        p.disconnect()
        return if (code in 200..299) "上傳成功（$code）" else "上傳失敗 HTTP $code"
    }
}

class PriceService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var wake: PowerManager.WakeLock
    private var busy = false

    override fun onCreate() {
        super.onCreate()
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "abi:watch")
        wake.setReferenceCounted(false)
        wake.acquire()
        startFg(notif("等待 Discord 指令…"))
        handler.postDelayed(tick, 4_000)
    }

    private val tick = object : Runnable {
        override fun run() {
            if (!busy) {
                Thread {
                    val req = Scrape.fetchRequest(this@PriceService)
                    val last = getSharedPreferences(PREFS, MODE_PRIVATE).getLong("lastReq", 0L)
                    if (req != null && req.first > last) {
                        busy = true
                        val minors = req.second.split(",").map { it.trim() }.filter { it.isNotEmpty() }
                        handler.post {
                            notify(notif("收到指令，抓取 ${minors.size} 個分類…"))
                            Scrape.run(this@PriceService, minors, req.first) { msg ->
                                getSharedPreferences(PREFS, MODE_PRIVATE)
                                    .edit().putLong("lastReq", req.first).putString("last", msg).apply()
                                notify(notif("$msg · ${nowText()}"))
                                busy = false
                            }
                        }
                    }
                }.start()
            }
            handler.postDelayed(this, 20_000)
        }
    }

    private fun nowText(): String =
        SimpleDateFormat("MM-dd HH:mm:ss", Locale.getDefault()).format(Date())

    private fun notif(text: String): Notification {
        val mgr = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26 && mgr.getNotificationChannel(CHANNEL) == null) {
            mgr.createNotificationChannel(
                NotificationChannel(CHANNEL, "ABI 價格回報", NotificationManager.IMPORTANCE_LOW))
        }
        val pi = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("ABI 價格回報")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    private fun startFg(n: Notification) {
        if (Build.VERSION.SDK_INT >= 29)
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else
            startForeground(1, n)
    }

    private fun notify(n: Notification) {
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(1, n)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startFg(notif("等待 Discord 指令…"))
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        if (wake.isHeld) wake.release()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?) = null
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        if (intent?.action == Intent.ACTION_BOOT_COMPLETED) {
            try {
                ctx.startForegroundService(Intent(ctx, PriceService::class.java))
            } catch (_: Exception) {
            }
        }
    }
}

object Updater {
    private const val APK_MIME = "application/vnd.android.package-archive"

    fun currentVersion(ctx: Context): String = try {
        ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "0"
    } catch (e: Exception) {
        "0"
    }

    fun newer(remote: String?, local: String): Boolean {
        fun parts(v: String) = v.trim().removePrefix("v").split(".")
            .mapNotNull { it.toIntOrNull() }
        val r = parts(remote ?: "")
        val l = parts(local)
        if (r.isEmpty()) return false
        for (i in 0 until maxOf(r.size, l.size)) {
            val a = r.getOrElse(i) { 0 }
            val b = l.getOrElse(i) { 0 }
            if (a != b) return a > b
        }
        return false
    }

    /** 查 GitHub 最新 release；callback 在主執行緒 */
    fun check(ctx: Context, cb: (String?, String?) -> Unit) {
        Thread {
            var tag: String? = null
            var url: String? = null
            try {
                val c = URL("https://api.github.com/repos/$REPO/releases/latest")
                    .openConnection() as HttpURLConnection
                c.setRequestProperty("Accept", "application/vnd.github+json")
                c.setRequestProperty("User-Agent", "abi-app")
                c.connectTimeout = 15000
                c.readTimeout = 15000
                val body = c.inputStream.bufferedReader().readText()
                c.disconnect()
                val o = JSONObject(body)
                tag = o.optString("tag_name")
                val assets = o.optJSONArray("assets")
                if (assets != null) {
                    for (i in 0 until assets.length()) {
                        val a = assets.getJSONObject(i)
                        if (a.optString("name").endsWith(".apk")) {
                            url = a.optString("browser_download_url")
                            break
                        }
                    }
                }
            } catch (e: Exception) {
            }
            Handler(Looper.getMainLooper()).post { cb(tag, url) }
        }.start()
    }

    fun download(ctx: Context, url: String, tag: String): Long? = try {
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val name = "abi-price-" + tag.trim().removePrefix("v") + ".apk"
        val req = DownloadManager.Request(Uri.parse(url))
            .setTitle("ABI 價格回報 $tag")
            .setMimeType(APK_MIME)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
        dm.enqueue(req)
    } catch (e: Exception) {
        null
    }

    /** 回 true 表示已開啟安裝流程，false 表示需要先允許安裝未知來源 */
    fun install(ctx: Context, id: Long): Boolean {
        if (Build.VERSION.SDK_INT >= 26 && !ctx.packageManager.canRequestPackageInstalls()) {
            try {
                ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + ctx.packageName))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (_: Exception) {
            }
            return false
        }
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val uri = dm.getUriForDownloadedFile(id) ?: return false
        val i = Intent(Intent.ACTION_VIEW).setDataAndType(uri, APK_MIME)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(i)
        return true
    }
}

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var patInput: EditText
    private lateinit var updateBtn: Button
    private var newTag: String? = null
    private var newUrl: String? = null
    private var pendingDl = -1L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        status = TextView(this).apply {
            text = "狀態：${prefs.getString("last", "尚未執行")}"
            textSize = 14f
            setPadding(0, 16, 0, 16)
        }
        patInput = EditText(this).apply {
            hint = "GitHub PAT（Contents: Read and write）"
            setText(prefs.getString("pat", ""))
            textSize = 13f
        }

        val ll = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 80, 48, 24)
        }
        ll.addView(TextView(this).apply { text = "ABI 價格回報"; textSize = 22f })
        ll.addView(TextView(this).apply {
            val v = try {
                packageManager.getPackageInfo(packageName, 0).versionName
            } catch (e: Exception) {
                "?"
            }
            text = "版本 v$v　·　多分類抓取"
            textSize = 12f
            setTextColor(0xFF666666.toInt())
            setPadding(0, 4, 0, 8)
        })
        ll.addView(status)
        ll.addView(patInput)

        ll.addView(Button(this).apply {
            text = "儲存 PAT"
            setOnClickListener {
                prefs.edit().putString("pat", patInput.text.toString().trim()).apply()
                status.text = "狀態：PAT 已儲存"
            }
        })
        ll.addView(Button(this).apply {
            text = "開啟懸浮窗權限（必要）"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName")))
            }
        })
        ll.addView(Button(this).apply {
            text = "立即抓取一次"
            setOnClickListener {
                status.text = "狀態：抓取中…"
                Scrape.run(this@MainActivity, listOf("40505"), 0L) { msg ->
                    status.text = "狀態：$msg"
                    prefs.edit().putString("last", msg).apply()
                }
            }
        })
        ll.addView(Button(this).apply {
            text = "啟動監聽（Discord 指令觸發）"
            setOnClickListener {
                if (Build.VERSION.SDK_INT >= 33) {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
                }
                startForegroundService(Intent(this@MainActivity, PriceService::class.java))
                status.text = "狀態：已啟動，等待 Discord 指令（每 20 秒檢查）"
            }
        })
        ll.addView(Button(this).apply {
            text = "停止"
            setOnClickListener {
                stopService(Intent(this@MainActivity, PriceService::class.java))
                status.text = "狀態：已停止"
            }
        })
        updateBtn = Button(this).apply {
            text = "檢查更新"
            setOnClickListener { checkUpdate(true) }
        }
        ll.addView(updateBtn)
        ll.addView(TextView(this).apply {
            text = "說明：不再定時抓取。Discord 有指令時，bot 會在 repo 寫請求，App 20 秒內看到就抓一次並上傳。"
            textSize = 12f
            setPadding(0, 24, 0, 0)
        })
        setContentView(ll)
        registerReceiver(dlReceiver, IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE),
            if (Build.VERSION.SDK_INT >= 33) Context.RECEIVER_EXPORTED else 0)
        checkUpdate(false)
    }

    private val dlReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, i: Intent?) {
            if (i?.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
            val id = i.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L)
            if (id > 0 && id == pendingDl) {
                pendingDl = -1L
                val ok = Updater.install(this@MainActivity, id)
                status.text = if (ok) "狀態：開始安裝…" else "狀態：請允許安裝未知來源後再按一次更新"
            }
        }
    }

    private fun checkUpdate(manual: Boolean) {
        val cur = Updater.currentVersion(this)
        if (manual) status.text = "狀態：檢查更新中…"
        Updater.check(this) { tag, url ->
            if (tag == null || url == null) {
                if (manual) status.text = "狀態：檢查失敗（網路或 API 限制）"
                return@check
            }
            if (Updater.newer(tag, cur)) {
                newTag = tag
                newUrl = url
                updateBtn.text = "下載 $tag"
                updateBtn.setOnClickListener {
                    val u = newUrl ?: return@setOnClickListener
                    pendingDl = Updater.download(this, u, newTag ?: "") ?: -1L
                    status.text = if (pendingDl > 0) "狀態：下載 $newTag 中…" else "狀態：下載失敗"
                }
                status.text = "狀態：有新版本 $tag（目前 v$cur），按下方按鈕下載"
            } else {
                updateBtn.text = "檢查更新"
                if (manual) status.text = "狀態：已是最新版 v$cur"
            }
        }
    }

    override fun onDestroy() {
        try { unregisterReceiver(dlReceiver) } catch (_: Exception) {}
        super.onDestroy()
    }
}
