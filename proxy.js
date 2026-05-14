// Vercel Serverless Function
// 用途：轉發對外 API 請求，繞過瀏覽器 CORS 限制
// 呼叫方式：/api/proxy?url=https://...

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();

  const { url } = req.query;
  if (!url) return res.status(400).json({ error: "Missing url parameter" });

  const ALLOWED = [
    "mis.twse.com.tw",
    "www.twse.com.tw",
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
  ];

  let targetUrl;
  try { targetUrl = new URL(url); }
  catch { return res.status(400).json({ error: "Invalid URL" }); }

  const allowed = ALLOWED.some(d => targetUrl.hostname === d || targetUrl.hostname.endsWith("." + d));
  if (!allowed) return res.status(403).json({ error: "Domain not allowed" });

  try {
    const response = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (compatible; TaiwanStockDashboard/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Referer": "https://tw.finance.yahoo.com/",
      },
      signal: AbortSignal.timeout(8000),
    });

    if (!response.ok) return res.status(response.status).json({ error: `Upstream error: ${response.status}` });

    const contentType = response.headers.get("content-type") || "application/json";
    const text = await response.text();
    res.setHeader("Content-Type", contentType);
    res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=30");
    return res.status(200).send(text);
  } catch (err) {
    return res.status(500).json({ error: "Proxy fetch failed", detail: err.message });
  }
}
