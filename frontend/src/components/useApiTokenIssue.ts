import { useState } from "react";
import { message } from "antd";

import { createApiToken, errMsg } from "../api";

/** 签发一枚开放 API 的个人 token,把**一次性明文**交到调用方手里。
 *
 *  两个入口共用这段流程:头像菜单的 ApiTokenModal(凭证管理)与任务页的 AgentSkillModal
 *  (拼给 AI Agent 的安装指令)。收成一处是因为流程里有两条极易抄漏的语义:
 *
 *   · **签发即重置** —— `POST /api/auth/api-token` 的意思就是「给我一个新的,旧的作废」,
 *     没有「只在没有时才创建」这一档;所以已有 token 的入口必须自带二次确认。
 *   · **明文只在这一次响应里有** —— 库里只存 SHA-256,后端自己也没有;拿到后**不回推任何
 *     状态**(想看新状态只能重新查),明文的存活范围就是一次弹窗会话,故 `reset()` 在打开时调。
 *
 *  谁漏抄了哪半句,症状都是「一个入口的行为悄悄与另一个分了岔」,编译期查不出来 ——
 *  useUserPicker 的 docstring 里记着同一种事故。
 *
 *  只管签发:两处的「查当前状态」需要的形状与失败处理都不同(一处弹错、一处静默降级),
 *  留在各自组件里。 */
export function useApiTokenIssue() {
  const [freshToken, setFreshToken] = useState<string | null>(null);
  const [issuing, setIssuing] = useState(false);

  const issue = async () => {
    setIssuing(true);
    try {
      const r = await createApiToken();
      setFreshToken(r.token);
    } catch (e: any) {
      message.error(errMsg(e, "生成 API Token 失败"));
    } finally {
      setIssuing(false);
    }
  };

  return { freshToken, issuing, issue, reset: () => setFreshToken(null) };
}
