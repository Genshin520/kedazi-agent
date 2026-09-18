// 支持跨网络块、UTF-8 多字节、CRLF 和尾部缓冲；不靠单次 read 的边界。
export async function* parseSSE(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
      let match;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const event = block.split(/\r?\n/).find(line => line.startsWith("event:"))?.slice(6).trim() ?? "message";
        const data = block.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (data) yield {event, data: JSON.parse(data)};
      }
      if (done) break;
    }
    if (buffer.trim() && !buffer.startsWith(":")) throw new Error("响应在完成前中断");
  } finally { reader.releaseLock(); }
}
