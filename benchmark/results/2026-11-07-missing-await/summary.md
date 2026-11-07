# The missing-await check on awaits it wasn't written for

- **Dropped awaits caught:** 42 of 56, each an `await` added by a merged PR in clean.json (outside the planted-bug benchmark's 40), dropped one at a time.
- **Findings on the 184 PRs as merged:** 0.

Missed: `return await super().download_request(request)` (scrapy/scrapy#8466); `await sleep(0.5)` (scrapy/scrapy#8440); `await _await_spider_output(self.parse_node(response, selecto` (scrapy/scrapy#8392); `await _await_spider_output(self.parse_row(response, row))` (scrapy/scrapy#8392); `return await collect_asyncgen(result)` (scrapy/scrapy#8392); `return await result` (scrapy/scrapy#8392); `items, _ = await crawl_items(spider_cls, mockserver)` (scrapy/scrapy#8392); `output = await output` (scrapy/scrapy#8391); `return await result` (scrapy/scrapy#8391); `assert await maybe_deferred_to_future(d) == [{"foo": "bar"}]` (scrapy/scrapy#8391); `await sleep(0)` (scrapy/scrapy#8386); `results = await super().resolve(host, 0, family)` (scrapy/scrapy#8382); `await self._resolver.close()` (scrapy/scrapy#8382); `response = await download_handler.download_request(request)` (scrapy/scrapy#8382).
