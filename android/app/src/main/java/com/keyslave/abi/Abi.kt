package com.keyslave.abi

import android.Manifest
import android.app.*
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.net.Uri
import android.os.*
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
    var v=c.querySelector('.market-item-lowest-value'), q=c.querySelector('.market-item-count');
    var t=v?v.textContent.trim():'';
    if(!/\d/.test(t)) continue;
    out.push({id:Number(c.dataset.itemId),
              name:n?(n.getAttribute('title')||n.textContent.trim()):'',
              low:t, count:q?q.textContent.trim():''});
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
                upload(ctx, all, reqId) { msg -> cb(msg) }
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

        fun startPoll() {
            if (done) return
            tries++
            if (tries > 40) { finish(null); return }
            web.evaluateJavascript(EXTRACT_JS) { raw ->
                if (done) return@evaluateJavascript
                val arr = parse(raw)
                if (arr != null && arr.length() > 0) finish(arr)
                else handler.postDelayed({ startPoll() }, 800)
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

    private fun upload(ctx: Context, arr: JSONArray, reqId: Long, cb: (String) -> Unit) {
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
                put("minor", "40505")
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

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var patInput: EditText

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
        ll.addView(TextView(this).apply {
            text = "說明：不再定時抓取。Discord 有指令時，bot 會在 repo 寫請求，App 20 秒內看到就抓一次並上傳。"
            textSize = 12f
            setPadding(0, 24, 0, 0)
        })
        setContentView(ll)
    }
}
