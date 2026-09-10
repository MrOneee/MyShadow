/** Classify fetched error/challenge pages without mistaking article text for a challenge. */
export function unreadablePage(value) {
  if (!value) return null;
  if (value.statusCode >= 400) return {code:'http_error',message:`HTTP ${value.statusCode}，没有取得可用正文，请换公开来源。`};
  if (value.body?.kind !== 'html') return null;
  const visible=String(value.body.content||'').replace(/<(script|style|noscript)\b[^>]*>[\s\S]*?<\/\1>/gi,'')
    .replace(/<[^>]+>/g,' ').replace(/&(?:nbsp|#160);/g,' ').replace(/\s+/g,' ').trim();
  if (!visible) return value.truncated
    ? {code:'html_truncated',message:'HTML 已达到读取上限，当前取得的部分只有脚本或样式，没有正文；请换直达正文的公开来源。'}
    : {code:'no_readable_text',message:'页面只有脚本或样式，没有可读取的正文；可能需要浏览器执行脚本。不是正文长度截断，请换来源，不反复改网址重试。'};
  if (visible.length<120 && /验证码|安全验证|访问验证|access denied|verify you are human|just a moment/i.test(visible))
    return {code:'challenge_page',message:'取得的是验证页，不是文章正文。请换来源。'};
  return null;
}
