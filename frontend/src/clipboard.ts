/** 复制到剪贴板,返回成功与否 —— 调用方据此给提示,**不许无条件报成功**。
 *
 *  两条路都要:navigator.clipboard 是安全上下文(https 或 localhost)专有的,线上那条
 *  https 的 /rubick 子路径没问题,但内网直接 http://<内网IP> 访问时它**整个不存在**。
 *  此前 SqlModal 写的是 `navigator.clipboard?.writeText(...)` 紧跟 message.success ——
 *  在那种环境下什么都没复制,却说已复制,用户拿着空剪贴板去粘,还以为是别处的问题。
 *
 *  回退用 execCommand("copy"):已废弃,但在用的浏览器都还支持,且不要求安全上下文。
 *  writeText 也会 reject(用户拒了权限、页面没焦点),所以 catch 之后还要落到回退再试一次,
 *  而不是直接判失败。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 落到下面的回退 */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // 不能 display:none —— 那样选不中也复制不了;挪到视口外即可
    ta.style.cssText = "position:fixed;top:-9999px;opacity:0";
    ta.setAttribute("readonly", ""); // 移动端别唤起键盘
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length); // iOS Safari 只认这一句
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
