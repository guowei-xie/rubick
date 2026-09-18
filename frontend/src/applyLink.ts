/** 发给「还没有飞书应用权限、因而登不进来」的同事的一整句。
 *
 *  URL 由后端 /auth/config 下发(settings.FEISHU_APP_APPLY_URL),引导语写在这里 ——
 *  换飞书应用时只换 URL,这句话不变,所以两者不该住在同一个配置项里。
 *
 *  **复制整句而不是裸链接**:一条 applink.feishu.cn/XXXX 的地址单独躺在聊天框里,
 *  收到的人既不知道那是什么、也不敢点。这句话与飞书自己分享应用时给的文案一致。
 */
export const applyShareText = (url: string) =>
  `我在飞书分享了一个应用给你，点击链接查看 ${url}`;
