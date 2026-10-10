/*
Run this script in the DevTools console on:
https://clipper.yinxiang.com/collectors/all?view=all

It uses the current logged-in browser session to call Yinxiang Clipper APIs,
then downloads one JSON capture file. Convert that JSON with:

python3 scripts/convert_yinxiang_clipper_capture_to_md.py \
  --input .clipper_probe/collectors_page_001.json \
  --output-dir yinxiang_clipper_collectors_page_001
*/

(async (options = {}) => {
  const pageNumber = Number(options.pageNumber || 1);
  const pageSize = Number(options.pageSize || 10);
  const sortingType = options.sortingType || "CREATE_TYPE";
  const orderType = options.orderType || "DESC";
  const pauseMs = Number(options.pauseMs || 120);

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function postJson(path, body) {
    const res = await fetch(path, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const text = await res.text();
    let json;
    try {
      json = JSON.parse(text);
    } catch (err) {
      throw new Error(`${path} returned non-JSON ${res.status}: ${text.slice(0, 200)}`);
    }
    if (!res.ok) {
      throw new Error(`${path} HTTP ${res.status}: ${text.slice(0, 200)}`);
    }
    return json;
  }

  function parseContentResponse(raw) {
    const packet = Array.isArray(raw) ? raw[0] : raw;
    const status = packet?.status || null;
    const rawContent = packet?.data?.content || packet?.content || "";
    let parsed = null;
    let cleanedHtml = "";
    let parseError = "";
    if (rawContent) {
      try {
        parsed = typeof rawContent === "string" ? JSON.parse(rawContent) : rawContent;
        cleanedHtml = parsed?.content?.cleanedHtml || parsed?.cleanedHtml || "";
      } catch (err) {
        parseError = String((err && err.message) || err);
      }
    }
    return {
      status,
      cleanedHtmlLength: cleanedHtml.length,
      parseError,
      content: {
        cleanedHtml,
        parsedKeys: parsed && typeof parsed === "object" ? Object.keys(parsed) : [],
        innerKeys: parsed?.content && typeof parsed.content === "object" ? Object.keys(parsed.content) : [],
      },
    };
  }

  const request = {
    searchType: "ALL",
    page: { pageNumber, pageSize },
    sorts: { sortingType, orderType },
  };
  const listJson = await postJson("/third/ever-collector/v2/getCollectionItemList", request);
  if (listJson?.status?.code !== 200) {
    throw new Error(`list API status ${JSON.stringify(listJson.status)}`);
  }

  const listItems = (listJson.data?.items || []).slice(0, pageSize);
  const capturedItems = [];
  for (let i = 0; i < listItems.length; i += 1) {
    const listItem = listItems[i];
    const itemGuid = listItem.itemGuid;
    const mate = await postJson("/third/ever-collector/v2/getCollectionItemMate", {
      itemGuid,
      lastSyncTime: "0",
    });

    let contentInfo = {
      status: null,
      cleanedHtmlLength: 0,
      parseError: "",
      content: { cleanedHtml: "" },
    };
    let contentError = "";
    try {
      const contentRaw = await postJson("/third/ever-collector/v2/getCollectionItemContent", { itemGuid });
      contentInfo = parseContentResponse(contentRaw);
    } catch (err) {
      contentError = String((err && err.message) || err);
    }

    capturedItems.push({
      index: i + 1,
      itemGuid,
      listItem,
      mateStatus: mate?.status || null,
      mateItem: mate?.data?.item || null,
      contentStatus: contentInfo.status,
      cleanedHtmlLength: contentInfo.cleanedHtmlLength,
      contentParseError: contentInfo.parseError,
      contentError,
      content: contentInfo.content,
    });
    await sleep(pauseMs);
  }

  const payload = {
    capturedAt: new Date().toISOString(),
    sourcePage: location.href,
    request,
    listStatus: listJson.status,
    paging: listJson.data?.paging || null,
    items: capturedItems,
  };

  const jsonText = JSON.stringify(payload);
  const filename = `yinxiang_clipper_collectors_p${String(pageNumber).padStart(4, "0")}_n${pageSize}_${new Date()
    .toISOString()
    .replace(/[:.]/g, "-")
    .slice(0, 19)}.json`;
  const blob = new Blob([jsonText], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(url);
    a.remove();
  }, 1000);

  return {
    filename,
    bytes: jsonText.length,
    listTotal: listJson.data?.paging?.total || null,
    itemCount: capturedItems.length,
    items: capturedItems.map((item) => ({
      index: item.index,
      guid: item.itemGuid,
      title: item.mateItem?.title || item.listItem?.title,
      type: item.mateItem?.itemType || item.listItem?.itemType,
      cleanedHtmlLength: item.cleanedHtmlLength,
      contentError: item.contentError,
      parseError: item.contentParseError,
    })),
  };
})({
  pageNumber: 1,
  pageSize: 10,
});
