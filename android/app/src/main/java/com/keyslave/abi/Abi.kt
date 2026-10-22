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
private const val APP_VER = "1.7"

private val EXTRACT_JS = """
(function(){
  var cards=document.querySelectorAll('#marketItemGrid .market-item-card');
  var out=[], unpriced=0;
  for(var i=0;i<cards.length;i++){
    var c=cards[i], n=c.querySelector('.market-item-name');
    var v=c.querySelector('.market-item-lowest-value');
    var q=c.querySelector('.market-item-count') || c.querySelector('.market-item-total-count') || c.querySelector('.market-item-stock');
    var t=v?v.textContent.trim():'';
    if(/--/.test(t)){ unpriced++; continue; }
    if(!/\d/.test(t)) continue;
    out.push({id:Number(c.dataset.itemId),
              name:n?(n.getAttribute('title')||n.textContent.trim()):'',
              low:t, count:(q&&q.textContent.trim())?q.textContent.trim():((c.textContent||'').match(/在售\s*([\d,]+)/)||[])[1]||''});
  }
  return JSON.stringify({total:cards.length, filled:out.length, unpriced:unpriced, items:out});
})()
"""

private val DIALOG_STEP_JS = """
(function(itemId, wantHist){
  if(!window._abiDlg || window._abiDlg.id !== itemId){
    var btn=document.querySelector('.market-item-card[data-item-id="'+itemId+'"] .market-details-button');
    if(!btn) return JSON.stringify({st:'skip'});
    window._abiDlg={id:itemId, phase:0, t0:Date.now(), rec:[], day:[], week:[], count:''};
    btn.click();
    return JSON.stringify({st:'wait'});
  }
  var s=window._abiDlg;
  if(Date.now()-s.t0>18000){
    try{document.querySelector('#marketItemClose').click();}catch(e){}
    window._abiDlg=null;
    return JSON.stringify({st:'timeout'});
  }
  var dlg=document.querySelector('#marketItemDialog');
  var bars=dlg?dlg.querySelector('.market-item-price-bars'):null;
  if(!bars) return JSON.stringify({st:'wait'});
  var cntEl=dlg.querySelector('.market-item-count');
  if(cntEl) s.count=cntEl.textContent.trim();
  if(!wantHist){
    try{document.querySelector('#marketItemClose').click();}catch(e){}
    window._abiDlg=null;
    return JSON.stringify({st:'done', count:s.count});
  }
  var det=dlg.querySelector('details.market-item-history');
  function rows(){
    if(!det) return [];
    var trs=det.querySelectorAll('tbody tr'), r=[];
    for(var i=0;i<trs.length;i++){
      var tds=trs[i].children, row=[];
      for(var j=0;j<tds.length;j++) row.push(tds[j].textContent.trim());
      if(row.length>=3 && /\d/.test(row[0])) r.push(row);
    }
    return r;
  }
  if(s.phase===0){
    if(det) det.open=true;
    s.phase=1; s.t1=Date.now();
    return JSON.stringify({st:'wait'});
  }
  if(s.phase===1){
    if(Date.now()-s.t1<2200) return JSON.stringify({st:'wait'});
    s.rec=rows();
    var db=dlg.querySelector('.market-history-range-btn[data-range="day"]');
    if(db) db.click();
    s.phase=2; s.t1=Date.now();
    return JSON.stringify({st:'wait'});
  }
  if(s.phase===2){
    if(Date.now()-s.t1<2200) return JSON.stringify({st:'wait'});
    s.day=rows();
    var wb=dlg.querySelector('.market-history-range-btn[data-range="week"]');
    if(wb) wb.click();
    s.phase=3; s.t1=Date.now();
    return JSON.stringify({st:'wait'});
  }
  if(s.phase===3){
    if(Date.now()-s.t1<2000) return JSON.stringify({st:'wait'});
    s.week=rows();
    try{document.querySelector('#marketItemClose').click();}catch(e){}
    var res={st:'done', count:s.count, recent:s.rec, day:s.day, week:s.week};
    window._abiDlg=null;
    return JSON.stringify(res);
  }
  return JSON.stringify({st:'wait'});
})
"""

data class ReqInfo(val id: Long, val minor: String, val mode: String)

object Scrape {
    @Volatile private var reqEtag: String = ""
    @Volatile private var cachedReq: ReqInfo? = null

    fun hasOverlay(ctx: Context) = Settings.canDrawOverlays(ctx)

    private fun pat(ctx: Context) =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("pat", "")?.trim().orEmpty()

    private fun api(pat: String, path: String): HttpURLConnection {
        val c = URL("https://api.github.com/repos/$REPO/$path").openConnection() as HttpURLConnection
        c.setRequestProperty("Authorization", "Bearer $pat")
        c.setRequestProperty("Accept", "application/vnd.github+json")
        c.setRequestProperty("User-Agent", "abi-price-app")
        c.connectTimeout = 10000
        c.readTimeout = 10000
        return c
    }

    /** 讀 scrape_request.json，使用 ETag 條件請求（304 不扣 GitHub API 配額，支援 5 秒極速輪詢） */
    fun fetchRequest(ctx: Context): ReqInfo? {
        val p = pat(ctx)
        if (p.isEmpty()) return null
        val c = api(p, "contents/scrape_request.json").apply {
            setRequestProperty("Accept", "application/vnd.github.raw")
            if (reqEtag.isNotEmpty()) setRequestProperty("If-None-Match", reqEtag)
        }
        return try {
            val code = c.responseCode
            if (code == 304) {
                cachedReq
            } else if (code == 200) {
                val et = c.getHeaderField("ETag")
                if (!et.isNullOrEmpty()) reqEtag = et
                val t = BufferedReader(InputStreamReader(c.inputStream)).readText()
                val o = JSONObject(t)
                val id = o.optLong("id", 0L)
                val r = if (id <= 0) null else ReqInfo(
                    id,
                    o.optString("minor", "40505"),
                    o.optString("mode", "grid")
                )
                cachedReq = r
                r
            } else null
        } catch (_: Exception) {
            null
        } finally {
            c.disconnect()
        }
    }

    /** 多 WebView 併發池（最多 3 個 Worker 平行抓取），最後一次合併上傳 */
    fun run(ctx: Context, minors: List<String>, reqId: Long, mode: String = "grid", cb: (String) -> Unit) {
        if (!hasOverlay(ctx)) { cb("缺少「顯示在其他應用程式上層」權限"); return }
        if (minors.isEmpty()) { cb("未指定分類"); return }
        val all = JSONArray()
        val histAll = JSONObject()
        val wantHist = (mode == "history")
        val lock = Any()
        var nextIdx = 0
        val workers = if (wantHist) 1 else minOf(3, minors.size)
        var activeWorkers = workers

        fun launchWorker() {
            val m: String
            synchronized(lock) {
                if (nextIdx >= minors.size) {
                    activeWorkers--
                    if (activeWorkers == 0) {
                        if (all.length() == 0) cb("沒有解析到任何價格")
                        else upload(ctx, all, histAll, reqId, minors, cb)
                    }
                    return
                }
                m = minors[nextIdx++]
            }
            scrapeOne(ctx, m, wantHist || m == "40505", wantHist) { arr, hObj ->
                synchronized(lock) {
                    if (arr != null) {
                        for (k in 0 until arr.length()) {
                            val item = arr.getJSONObject(k)
                            item.put("minor", m)
                            all.put(item)
                        }
                    }
                    if (hObj != null) {
                        val ks = hObj.keys()
                        while (ks.hasNext()) {
                            val k = ks.next()
                            histAll.put(k, hObj.getJSONObject(k))
                        }
                    }
                }
                launchWorker()
            }
        }
        for (w in 0 until workers) launchWorker()
    }

    /** 抓單一分類（1px 隱藏 WebView，關閉圖片加速渲染，filled==total 0延遲收工） */
    private fun scrapeOne(
        ctx: Context,
        minor: String,
        wantCount: Boolean,
        wantHist: Boolean,
        cb: (JSONArray?, JSONObject?) -> Unit
    ) {
        val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val web = WebView(ctx)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.blockNetworkImage = true
        web.settings.loadsImagesAutomatically = false
        web.settings.userAgentString =
            "Mozilla/5.0 (Linux; Android 14; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        val lp = WindowManager.LayoutParams(
            1, 1, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT
        ).apply { gravity = Gravity.TOP or Gravity.START; alpha = 0.01f }
        try { wm.addView(web, lp) } catch (e: Exception) { cb(null, null); return }

        var done = false
        var tries = 0
        val handler = Handler(Looper.getMainLooper())
        lateinit var poll: () -> Unit

        val merged = JSONObject()
        val histMap = JSONObject()
        var prevSig = ""
        var stable = 0

        fun mergedArray(): JSONArray {
            val out = JSONArray()
            val keys = merged.keys()
            while (keys.hasNext()) out.put(merged.getJSONObject(keys.next()))
            return out
        }

        fun finish(arr: JSONArray?) {
            if (done) return
            done = true
            handler.removeCallbacksAndMessages(null)
            try { web.destroy(); wm.removeView(web) } catch (_: Exception) {}
            cb(arr, if (histMap.length() > 0) histMap else null)
        }

        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) { poll() }
        }

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

        fun expandDetails(ids: List<String>, idx: Int) {
            if (done) return
            if (idx >= ids.size) {
                finish(mergedArray())
                return
            }
            val iid = ids[idx]
            val js = "$DIALOG_STEP_JS($iid, ${if (wantHist) "true" else "false"})"
            web.evaluateJavascript(js) { raw ->
                if (done) return@evaluateJavascript
                val s = try { JSONTokener(raw ?: "").nextValue() as? String } catch (_: Exception) { null }
                val o = try { if (s != null) JSONObject(s) else null } catch (_: Exception) { null }
                val st = o?.optString("st") ?: "skip"
                if (st == "wait") {
                    handler.postDelayed({ expandDetails(ids, idx) }, 280)
                } else {
                    if (st == "done" && o != null) {
                        val cnt = o.optString("count", "")
                        if (cnt.isNotEmpty() && merged.has(iid)) {
                            merged.getJSONObject(iid).put("count", cnt)
                        }
                        if (wantHist) {
                            histMap.put(iid, JSONObject().apply {
                                put("recent", o.optJSONArray("recent") ?: JSONArray())
                                put("day", o.optJSONArray("day") ?: JSONArray())
                                put("week", o.optJSONArray("week") ?: JSONArray())
                            })
                        }
                    }
                    handler.postDelayed({ expandDetails(ids, idx + 1) }, 150)
                }
            }
        }

        fun onGridStable() {
            if (!wantCount && !wantHist) {
                finish(mergedArray())
                return
            }
            val ids = ArrayList<String>()
            val ks = merged.keys()
            while (ks.hasNext()) ids.add(ks.next())
            expandDetails(ids, 0)
        }

        fun startPoll() {
            if (done) return
            tries++
            if (tries > 65) {
                if (merged.length() > 0) onGridStable() else finish(null)
                return
            }
            web.evaluateJavascript(EXTRACT_JS) { raw ->
                if (done) return@evaluateJavascript
                val stateObj = parseState(raw)
                val arr = stateObj?.optJSONArray("items")
                val total = stateObj?.optInt("total", 0) ?: 0
                val filled = stateObj?.optInt("filled", 0) ?: 0
                val unpriced = stateObj?.optInt("unpriced", 0) ?: 0
                if (arr != null && arr.length() > 0) mergeInto(arr)
                // 快速判定：若所有卡片皆已填入價格（或標示 -- 無掛賣），直接收工不必多等
                if (total > 0 && filled > 0 && (filled + unpriced) >= total) {
                    onGridStable()
                    return@evaluateJavascript
                }
                val sig = signature()
                if (merged.length() > 0 && sig == prevSig) stable++ else stable = 0
                prevSig = sig
                if (merged.length() > 0 && stable >= 2) onGridStable()
                else handler.postDelayed({ startPoll() }, 350)
            }
        }

        poll = { startPoll() }
        handler.postDelayed({
            web.loadUrl("https://abi-tracker.azurewebsites.net/Market/View?minorId=$minor")
        }, 100)
        val maxWait = if (wantHist) 180_000L else 45_000L
        handler.postDelayed({ if (!done) finish(if (merged.length() > 0) mergedArray() else null) }, maxWait)
    }

    private fun parseState(raw: String?): JSONObject? {
        if (raw == null || raw == "null") return null
        return try {
            val s = JSONTokener(raw).nextValue() as? String ?: return null
            JSONObject(s)
        } catch (e: Exception) { null }
    }

    private fun digits(s: String?): Long =
        (s ?: "").filter { it.isDigit() }.toLongOrNull() ?: 0L

    private fun upload(
        ctx: Context,
        arr: JSONArray,
        histAll: JSONObject,
        reqId: Long,
        minors: List<String>,
        cb: (String) -> Unit
    ) {
        val p = pat(ctx)
        if (p.isEmpty()) { cb("缺少 GitHub PAT"); return }

        Thread {
            val ts = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
                .apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date())

            val newItemsById = LinkedHashMap<String, JSONObject>()
            for (k in 0 until arr.length()) {
                val o = arr.getJSONObject(k)
                val low = digits(o.optString("low"))
                if (low <= 0) continue
                val idStr = o.optLong("id").toString()
                val m = o.optString("minor", "")
                newItemsById[idStr] = JSONObject().apply {
                    put("id", o.optLong("id"))
                    if (m.isNotEmpty()) put("minor", m)
                    put("name", o.optString("name"))
                    put("low", low)
                    put("count", digits(o.optString("count")))
                    put("ts", ts)
                }
            }
            if (newItemsById.isEmpty()) {
                Handler(Looper.getMainLooper()).post { cb("沒有解析到任何價格") }
                return@Thread
            }

            val msg = try {
                // 先讀取現有 phone_prices.json，合併保留未抓分類的資料（防止抓鑰匙時洗掉雜物）
                val (oldSha, oldJson) = getFileWithContent(p, "phone_prices.json")
                val catTs = JSONObject()
                val oldCatTs = oldJson?.optJSONObject("category_ts")
                if (oldCatTs != null) {
                    val ks = oldCatTs.keys()
                    while (ks.hasNext()) {
                        val k = ks.next()
                        catTs.put(k, oldCatTs.optString(k))
                    }
                }
                for (m in minors) catTs.put(m, ts)

                val mergedMap = LinkedHashMap<String, JSONObject>()
                val prevCountMap = HashMap<String, Long>()
                val scrapedSet = minors.toSet()
                val oldItems = oldJson?.optJSONArray("items")
                if (oldItems != null) {
                    for (i in 0 until oldItems.length()) {
                        val it = oldItems.optJSONObject(i) ?: continue
                        val idStr = it.optLong("id").toString()
                        val oldCnt = it.optLong("count", 0L)
                        if (oldCnt > 0L) prevCountMap[idStr] = oldCnt
                        val im = it.optString("minor", "")
                        // 只保留「非本次重抓分類」的既有商品，或被本次新資料覆蓋
                        if (im.isEmpty() || im !in scrapedSet) {
                            mergedMap[idStr] = it
                        }
                    }
                }
                for ((idStr, obj) in newItemsById) {
                    val prevCnt = prevCountMap[idStr] ?: 0L
                    if (obj.optLong("count", 0L) <= 0L && prevCnt > 0L) {
                        obj.put("count", prevCnt)
                    }
                    mergedMap[idStr] = obj
                }

                val finalItems = JSONArray()
                for (obj in mergedMap.values) finalItems.put(obj)

                val payload = JSONObject().apply {
                    put("fetched_at", ts)
                    put("app_version", APP_VER)
                    put("minor", minors.joinToString(","))
                    put("source", "android-app")
                    put("req", reqId)
                    put("category_ts", catTs)
                    put("items", finalItems)
                }
                val r1 = putFile(p, "phone_prices.json", payload.toString(1), "phone prices $ts (req $reqId)", oldSha)
                if (histAll.length() > 0) {
                    val hPayload = JSONObject().apply {
                        put("fetched_at", ts)
                        put("req", reqId)
                        put("items", histAll)
                    }
                    putFile(p, "phone_history.json", hPayload.toString(1), "phone history $ts (req $reqId)", null)
                }
                r1
            } catch (e: Exception) {
                "上傳失敗: ${e.message}"
            }
            Handler(Looper.getMainLooper()).post { cb(msg) }
        }.start()
    }

    private fun getFileWithContent(pat: String, name: String): Pair<String?, JSONObject?> {
        var sha: String? = null
        var json: JSONObject? = null
        val g = api(pat, "contents/$name")
        try {
            if (g.responseCode == 200) {
                val body = BufferedReader(InputStreamReader(g.inputStream)).readText()
                val meta = JSONObject(body)
                sha = meta.optString("sha").ifEmpty { null }
                val b64 = meta.optString("content", "").replace("\n", "").replace("\r", "")
                if (b64.isNotEmpty()) {
                    val raw = String(android.util.Base64.decode(b64, android.util.Base64.DEFAULT))
                    json = JSONObject(raw)
                }
            }
        } catch (_: Exception) {
        } finally {
            g.disconnect()
        }
        return sha to json
    }

    private fun putFile(pat: String, name: String, content: String, message: String, knownSha: String? = null): String {
        var sha: String? = knownSha
        if (sha == null) {
            val g = api(pat, "contents/$name")
            if (g.responseCode == 200) {
                val body = BufferedReader(InputStreamReader(g.inputStream)).readText()
                sha = JSONObject(body).optString("sha").ifEmpty { null }
            }
            g.disconnect()
        }

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
                    if (req != null && req.id > last) {
                        busy = true
                        val minors = req.minor.split(",").map { it.trim() }.filter { it.isNotEmpty() }
                        handler.post {
                            notify(notif("收到指令 #${req.id}，併發抓取 ${minors.size} 個分類 (${req.mode})…"))
                            Scrape.run(this@PriceService, minors, req.id, req.mode) { msg ->
                                getSharedPreferences(PREFS, MODE_PRIVATE)
                                    .edit().putLong("lastReq", req.id).putString("last", msg).apply()
                                notify(notif("$msg · ${nowText()}"))
                                busy = false
                            }
                        }
                    }
                }.start()
            }
            handler.postDelayed(this, 6_000)
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
            text = "版本 v$v　·　3路併發極速抓取"
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
            text = "立即抓取一次（鑰匙＋在售量）"
            setOnClickListener {
                status.text = "狀態：抓取中…"
                Scrape.run(this@MainActivity, listOf("40505"), 0L, "grid") { msg ->
                    status.text = "狀態：$msg"
                    prefs.edit().putString("last", msg).apply()
                }
            }
        })
        ll.addView(Button(this).apply {
            text = "更新鑰匙 30 天歷史"
            setOnClickListener {
                status.text = "狀態：展開詳情抓取 30 天歷史中（約 1~2 分鐘）…"
                Scrape.run(this@MainActivity, listOf("40505"), 0L, "history") { msg ->
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
                status.text = "狀態：已啟動，等待 Discord 指令（每 6 秒極速檢查）"
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
            text = "說明：Discord 有指令時，App 6 秒內透過 ETag 發現請求，以 3 路併發隱藏 WebView 抓取並合併上傳。"
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
                if (ok) {
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                        .remove("dlId").apply()
                    status.text = "狀態：開始安裝…"
                } else {
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                        .putLong("dlId", id).apply()
                    status.text = "狀態：請允許安裝未知來源，回來後會自動安裝"
                }
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

    override fun onResume() {
        super.onResume()
        val id = getSharedPreferences(PREFS, MODE_PRIVATE).getLong("dlId", -1L)
        if (id > 0L && Updater.install(this, id)) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove("dlId").apply()
            status.text = "狀態：開始安裝…"
        }
    }

    override fun onDestroy() {
        try { unregisterReceiver(dlReceiver) } catch (_: Exception) {}
        super.onDestroy()
    }
}
