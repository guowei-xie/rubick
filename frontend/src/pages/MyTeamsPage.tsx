import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Alert, Card, Col, Empty, Row, Space, Spin, Tag } from "antd";
import { Team, listTeams } from "../api";
import { isPlatformAdmin, useAuth } from "../auth";

/**
 * 「我的团队」—— 开发者的团队入口。
 *
 * 恰好属于一个团队时(最常见)直接跳进去,不让人多点一次。
 * 一个团队都没有时给出明确指路:那正是「新建任务」按钮为什么是灰的。
 */
export default function MyTeamsPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [teams, setTeams] = useState<Team[] | null>(null);

  useEffect(() => {
    listTeams().then(setTeams);
  }, []);

  if (teams === null) return <Spin style={{ margin: 80 }} />;

  // 平台管理员看得到全部团队,不该被自动带进某一个 —— 那不是「我的团队」
  if (teams.length === 1 && !isPlatformAdmin(user))
    return <Navigate to={`/teams/${teams[0].id}`} replace />;

  return (
    <Card title="我的团队" style={{ borderRadius: 28, minHeight: "calc(100vh - 120px)" }}>
      {teams.length === 0 ? (
        <Empty
          style={{ padding: "60px 0" }}
          description={
            <div style={{ maxWidth: 420, margin: "0 auto", color: "#8c8c8c" }}>
              你还不属于任何团队。
              <br />
              任务必须归属一个团队(它决定谁看得到、以及取数用哪套数据库账号),
              所以<b>在加入团队之前无法新建任务</b>。请联系平台管理员把你加入一个团队。
            </div>
          }
        />
      ) : (
        <>
          {isPlatformAdmin(user) && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="你是平台管理员，这里列出的是平台上的全部团队"
              description="平台管理员不受团队约束：可见并可编辑所有团队的任务。团队的增删改与团队管理员指派在「团队管理」页。"
            />
          )}
          <Row gutter={[16, 16]}>
            {teams.map((t) => (
              <Col key={t.id} xs={24} sm={12} lg={8}>
                <Card
                  hoverable
                  style={{ borderRadius: 20 }}
                  onClick={() => nav(`/teams/${t.id}`)}
                  title={
                    <Space>
                      {t.name}
                      {t.admins.some((a) => a.user_id === user?.id) && (
                        <Tag color="purple">团队管理员</Tag>
                      )}
                      {t.admins.length === 0 && <Tag color="orange">无团队管理员</Tag>}
                    </Space>
                  }
                >
                  <div style={{ color: "#8c8c8c", minHeight: 44 }}>{t.description || "—"}</div>
                  <Space size={16} style={{ color: "#8c8c8c", fontSize: 12 }}>
                    <span>{t.member_count} 名成员</span>
                    <span>{t.template_count} 个任务</span>
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
        </>
      )}
    </Card>
  );
}
