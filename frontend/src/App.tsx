import { Fragment, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ensureAnonymousIdentity, fetchMapData, fetchTrendingNotes, generateGuideRoutesStream, planFromRouteStream, reviseTripFromText, type PlanStageEvent, type TrendingInspiration, type WeatherBrief } from "./api";
import { RouteMap, type RouteMapHandle } from "./components/RouteMap";
import type { MapData, MapPoint, RouteLeg, RouteOption, RouteStep, RouteSummary, Transportation, TrendingNote } from "./types";
import "./styles.css";

const asset = {
  hero: "https://www.figma.com/api/mcp/asset/3a7501ab-e051-41d4-8655-f6ddd0abea0a.png",
  xian: "https://www.figma.com/api/mcp/asset/e8c56101-f984-45ef-888a-7ecc14f5d8b2.png",
  avatar: "https://www.figma.com/api/mcp/asset/c276af9a-a577-491a-84d2-86e19825b8b6.png",
  tripA: "https://www.figma.com/api/mcp/asset/8fa5a92f-4a6a-4f14-a7e1-660a32151b4d.png",
  tripB: "https://www.figma.com/api/mcp/asset/ce083833-b5fe-42b2-af25-ea551713a082.png",
  placeA: "https://www.figma.com/api/mcp/asset/c3340130-22ba-4c1d-9050-ce30591bd080.png",
  placeB: "https://www.figma.com/api/mcp/asset/b8161556-d138-46ce-a336-f8af1f86c054.png",
  logo: "https://www.figma.com/api/mcp/asset/55cf3414-8a99-4c8e-a351-975b7083f1dc.png",
};

// 1:535 首页：Stitch 导出的 5 张扇形旅行照片（Google 托管 URL，按规则直接复用）
const homeFan = {
  logo: "https://lh3.googleusercontent.com/aida-public/AB6AXuDMr3t_h3d1_Nojen42-Dv75VkYR6h4pbBVpgCuJRxinNAyqCiYmZDee2Kn3gE0GXZfZW_OmNingSRozW3ZtO-aSAbU1iPeO--8xZsZ-56wfzPGj-pEdoKyl4XvT7_lMNEOE91w_aEWlOJ2bHvHN29CpUyum7tIIUHZ-zFHznFt8neo2-KQ0NzlD4kPkUYsbXf_mu3CGwID4qRBE51pZM3yKxnWTPsS3c686wUAJVeQPbu0YoXLb-JK9HmFNaLMuWssyr2shrY6Nh064w",
  c1: "https://lh3.googleusercontent.com/aida-public/AB6AXuAmiJeTfB0z9OFptAlhQmzFoNDYFodXWrEt2qXEg5NSCKzAn34BDt8XWvDKyOSj9j0clBJFIXZZtTcY77UseOupsyqpMH3ClBe4i7KwSAOKHlMOWeCjkLEZDJtXbX6dqyC7mU2pQvznxvunaNIXzWKr1anowQooO6jgnIXS2W3O3GuM3l-TrNjdg4VmVuwfW9S9OfFxhNmZ_-vBNhLURK4Q3CzFEuTlF7OUYMlqWZvYusVYfC8CQ3sM",
  c2: "https://lh3.googleusercontent.com/aida-public/AB6AXuC96JSOBT-0BKfK-0uCtQ-C-lH5i-JWSLqj7TwbyLgwJ9CEQUYrkyCgzAu_RDMn5ibID6Ksabypc0M9vOJou9jnKyJtqgUt9B_EBoBGSyZQql-27l32HjGdr7aQgoESwSCNMHXwZgGJU3HiOg66-QNh0fbKAjK3RlvgOLG2rXs5HqDwX7X2JFgCrO7wI3vX7IgJwypEgPUrQGI8kXOG5B1LT4xdzXf0fSaa3xwWSxm4iG8NMI21GufG",
  c3: "https://lh3.googleusercontent.com/aida-public/AB6AXuAFfRTd3WuiX_GzPameZ4mWWNVTGcDDVHu9azNpeT6mTrePW_gM9tvITc2eGFqLVC4qHLG1oEfGcjYthWGx1sh9GUyLxOiRk-ddNlDvoZrd2eQE9mC58dbSWRAu8TI3yV37k7azqoTYfuSbI8EXSO_b-XSA31ZVx--kAMZZl-xc0_5IN7wXnIjSa2F1hrXerzB1VAqRT5jQ00Nhu4VAcivuE-BLXmD6QkVoks7KR3CFVDEiYdM63IIh",
  c4: "https://lh3.googleusercontent.com/aida-public/AB6AXuCn4nzG5jCSE3yXwVT2bkpK7k1FDSOV2bu98hbzXA0kC5xSz2F2Cm9pKuMcdm-oRecQLZbP4qwY-ZMEESW_pYDjdLOmNACNvDpDlLg14FqCUSJDsebdvCLPy_Z2I4Opyj9mSRczbNTwSwLl1oGAVLB06i6hb_oAIOPrHtA0yjSnBkY9x3t0zIE3HwtIX2_2TQNNbSyousqgZ9Jx19Mt6rWbsrOHNvUKm-LKB6SFW4LtXF-nHRaNVxnv",
  c5: "https://lh3.googleusercontent.com/aida-public/AB6AXuA4M-I22PWBDV-3i4lz3_FG6iPdO1Yu4IeOcDbOgyinGs8Q0Nekb1VQF_lvnwiQYmAGhC9MrSr2m0pv0MQ8XGl_I1Jp8Sy-ysr4Rz0v39lMI3ittV3csPhqnvvbNFYFkrCoxX7-aWESGN-0e_6ENvcrHR3d-7Z5b3Id2lUe7kZCw0XmuJCZHrmsKqkjUCdIaHi4P8y2xwxJAf1hBD05-fc1adpedteqE9KQKyqh9vrdEWN2J2SCDEu8",
  navAi: "https://lh3.googleusercontent.com/aida-public/AB6AXuBXUHMxaiLoOEDJVmYZEYfnleVDooqlMWPbqvi2GzEzpTDo0S4K9Cr1kHJ-o8dSiznmvvoBz-iPlYal5dQJiTH1q8cHs5MmmiZEFLgi3Rf10dDytosIU4vvFJ69qBppHBtJJxrCqfBPV2KmfSE5Rbv4lAOT5QK0ccL-64WZqoWsowEPw_8UV46RP5yzEUM0GPRql7ByPfB-oP55DrPW4bY9e98yFqe5VXaTP25amaMXtaGtxhPu5KIBVzAcJEo7TWaxs7V4yrPuPF1ung",
  // 对话页 header logo（新稿 9 用的圆形 logo）
  chatLogo: "https://lh3.googleusercontent.com/aida-public/AB6AXuA1U0pumTxnkP1DLiXUxxFR_wZ-zRypCyIK_nv5M3c0VbeGCY3vyOLnFJ4r-C9_FSR3erkuzLJv-U9g-A2Cei685KjVMUcJWw6oGdBOtO9-qRea88VqpMOlDGGaVnFSipmozGTyKO6IjwrkXiztU65BHVHKO0_mFaQ7ggLdECCKh1qUvKYG-Xdo1j8DYk0MduQE6Opp4XAKXyAUZvIeYrXEUXjWFzSF2sytmy_z69TOYrdT9FE8wqtMfnyYTECSe05c5g",
  // AI 头像（新稿 9 用的真实图片）
  aiAvatar: "https://lh3.googleusercontent.com/aida-public/AB6AXuBPxXwjB9PemhWlOdRkvbEE3o30Te3mLzdVTmbmXBHaNTfBC4wq5jAVT9pd4LcaXns78bqcuvRo0yQGUAhgBQmbAaXw5UqU6_-ODI7ua9Rluot-3ZOHkBCeeqZI65QNlFLJ16vKhACy8aIyLYSS5iehjfbHfmbh7BM-D7Zd4y3uLM5fn0Zqe5DaB29w_Ye9LetQRLSuwaGefopra8hXjgobE00Ty5jToynhwwY1Lu3tZaWwf0-BE6lcVCGvgDag8sBirw",
  // dock 小渡AI icon（新稿 9 用的）
  navAiNew: "https://lh3.googleusercontent.com/aida-public/AB6AXuCyoD38mrQNgFMpXZjc50Ns6KU4JbcdIzp3nd1yeCgo4kNzlqLqA1ZAyiNVbFr0EM1UYjkfaQkZvG3slE0SGpNXIYwdz-xDtCgorjDMdmdKOZ51fP60xHR0vBFt8BieLzE1qyJVaa4l2vwuwI3mXN2hAXrjOgcjCj3fKwBEU8DJis51arPu_6aOzomHOV-RfgYi1f12B6i9sBjn-KPkfubDqo4b-UvK8tSgveDNNPwiPA-7bthllh_e4vQRqIH6EC2_cnKUWCkvQz76vQ",
  // 侧边栏用户头像
  userAvatar: "https://lh3.googleusercontent.com/aida-public/AB6AXuA5W4MCErevu6fpW77Zuu43tTITgaOR0djfdSngppOYCr0Zjy7g6AbryGIkJd323lp9AdxNX1FwD6I77gzoksxWOYKAZq6AFdmI1qG8rrVvYXVc9qbWkqAXE1EElFEFCHoTfROjPTvBw5-fPjSh2zBojBculNKRIuoL3SkEGEwuKcaSxPD3-zKcqOZuX0_hCADqUgaURM18g3lqvPcqiljJLzcvDW2FHO69CNbyB8AEwp2JaYyNh3cIIBt8iEt3QoJb7j52AG17cB0hZA",
  // 小渡推荐页
  heroNanjing: "https://lh3.googleusercontent.com/aida-public/AB6AXuDxVbY4VvGkkwurw9-Gig7ZOPifpv5WDMb9jHwIbnxtGp_ysxMLF1AwUa5cJ8PBmf5gQaBa4bIMZomrDArPVOpF7E6GA7LBny5rvBVJo2R24Lo1ZGxJFANKTaB6xv0MGlxyO-ELHEvsIjkFkJ_uHZajt3_FJzo2gdqXnoHU0_hE7S5YXP8aT5CaH3Yl2IxgJXeI_UFBvIEsmW5KtpChgimhoBcLVexVObGi-YW7CqgWQ6WKZBqat0gn",
  xianNight: "https://lh3.googleusercontent.com/aida-public/AB6AXuBYgPMoLVtpAFuXlpOcYk1VX6mexl2kXYRLlBthp6zRPZg9-8iw4ZDSGpEOum7PYuR64SHaRI6XtGx95Okvh54r_V2hkHsNBGXth5PfILek3CE150Wz8oFMdQBuhaAxsxHSS9ahuI9DeA3etK4rsZPrvbZDU_XMQffq6AqlyzOCZ1tNQGBfCCKNrTBN2h8tRwmSw4eBMLPcOIXHMP99r63kDsiiRPn0ahHy-OvidM99wtZGoqpByY6h",
  avatarA: "https://lh3.googleusercontent.com/aida-public/AB6AXuDf9ATCV8YtyQh_qjIu3kDAtaTyWmUDriMr7y0opEJKahhPNwhbay05OqKT36-y2CyodNjNDp_qh9FbFvHnkyHD6BgcVSljZSqguS61BoM_hgHrZKtHuTd0lebGZ8XuQawxF1IHzRjBPY58dyxR1YmXl4yfGMpQ03sFdVC2gZER9mbA7Kv-58z0689PAMK3NlqBz8YDMNxMTBY93BpQ843oM17NdS9rw_F6IowMXKYhp6iOLCcSX4Vk",
  avatarB: "https://lh3.googleusercontent.com/aida-public/AB6AXuAykri4c6DqzANOq8TJZCqOrjRzPcGFAhQiFC_BRA4Oj1kuKgF1OywUEvGghfC9_tD0921L3GuaSNKwEGdaAhIad-kJIBYUk16FBetYq6MZAuoOAAKTm9W-RrJuDFFAojwpzlYJ1dEw_4zV4IMakqCAiLTRnBcegiD9sryTN39TgJ3FGNqCJ8Ke66OmTY80AOg4g18_zsvpJkT-XXxGFN9jVoijPfnMZ-El2377b403tEaoSlhOaojr",
  navAiInspire: "https://lh3.googleusercontent.com/aida-public/AB6AXuA-UXu2ctFxmevQqRP__hk2sCOeSfo6gHucc0RUY0tL6-0oWKecgy4hGQA7qZHYeO3Bmr_BAUh8E4YJ8ZpwCKtMeFj6YjtOWWQfIjIuiGhYXM0Cv5GElWHXcmqPB7WW2XUmqCttoPSaX6x4CRuk6n4abTaxHuH35SaoWKWexW6-Nf6C0258uPPCY6_k7512BhoLA1HNsEDgbPDdvhszd6TvaeclQW59ZThM5ISV-x3SSRuJZqHt1oCotQaKPZSjHO7Ea0Y5WNQvOViXhw",
  oracleBack: "https://lh3.googleusercontent.com/aida-public/AB6AXuDbDyUZkg5dAWTtM1nqJn0yoE4acpz7AGZYxbsPVQHYvhMuN9wiVpnQvLFqne9tUP-wwamQOWzyzfmA5eM-3ggKYUblUmsyWIhz3nKgfRXfw7t85738Gg6LgR5kGFEE7cipdme2xb09jIWQW2mPwi4oSx_4GHvR6b1SbFp8vB3Wh4JoODaudGImQmEdv23UZUzKm0mS1ym1LwsHx0biFGwferoDbCRnzqc6mjz1DOyka5WB4xOYzWa7c5Fu6520BeIuQA",
  oracleFront: "https://lh3.googleusercontent.com/aida-public/AB6AXuD7CBEp2dHOIwAiGxZjPul1RvpCYRzJc68oHhAfdaFiOoJL77I89PP1MpaMOJCRM8CvMhQ7jOrDnJp48llPKQdPrevZHT44uJNtP-O-W8twNneGYSmKTUpdjWwVezCHOzdQra7WOg4AT_6D12j2h5KxPyq1izHF38QCKids9swb-uHaQNeokK-6uZZrnr5rjDBsmVY5ia3hBRcvn0-g9EdxQPqId5itnznfgfRdjotTBR4xW36lfmXiHrSFpDty0CJwnw",
  tripZhongshan: "https://lh3.googleusercontent.com/aida-public/AB6AXuAIvf1vuK-_egMTeGfDM2SpCqLqz_Ukl_qq8IFCWQ08S9_CjkN-ijFXJcf9v1tOSar6kCHQXNZ0hbJP_sttfXg1Vchn4NShzEESR-PzAg_aXcAcLZMXQQb-PP58TdugP2Y3yDe4KrmSbzpS6fUee9lssknJOml9WXAUkvf6e4cBuOyDSFYUtzfw3yOJC2POedxtSerOt0ZuqIgEdth8qHzcCg47Q6Oa2461YcmfHygc1gJLHzUiU36m",
  tripMusic: "https://lh3.googleusercontent.com/aida-public/AB6AXuDRd4n3lllJ-rgLAEzjZDtOQqelC7A-pnBBpItOHpcLasQ7CcrGS29Y19Qb4kJJGvQw1PPNbwx8Ox-DboExe8JjeMxVuEX9OBvVL-xhnBM1cOJW0u4Vi3osYvbSqHvXpHWWcIDKOGV3Q0EXPxtsP1-4UYoQyB61t1N-yJvcifSYMPI5De7SHT3-r7QWrCEl1OAt2eCOQY2XJWx7eqnnCBgtqpjrh25CkuvaqgI9YsLchbZhwyG0NtiI",
  tripMing: "https://lh3.googleusercontent.com/aida-public/AB6AXuB6Y_hCVbHKnzNgaRaBESfXZi_tLLSTUuj1x3ywLbGXbrK_IVM6XuTaHw5QY8SZfwwueisE8VJLl-sxR4_L4MyBN9bKe1IoM7lCblKlCheY5SEAiSl8ikfpz4-NnzjgxVefnZIRhmUs-0sAfEnSykCmAfehiwHOcBT3ONhVweUa2HcYwT0kV0XDAyehm_lp8FflX0siAtJPl2pDyeZmLD2wC6aJFerRmDrBoqRIiK0Av6RoR4YIZixo",
};

const modes: Transportation[] = ["公共交通", "驾车", "步行", "骑行"];
type TripSeed = { city: string; query: string; theme?: string };
type TripSubView = "overview" | "detail" | "navigating";
type TransitPlanRow = { kind: "walk" | "transit"; title: string; detail: string };

type CityStop = { name: string; longitude: number; latitude: number; category: string; note: string };
const cityStops: Record<string, CityStop[]> = {
  南京: [
    { name: "中山陵", longitude: 118.856, latitude: 32.058, category: "历史文化名胜", note: "沿着林荫石阶慢慢向上，感受钟山清晨的安静。" },
    { name: "音乐台", longitude: 118.85, latitude: 32.055, category: "喂白鸽 · 休闲打卡", note: "在环形草坪稍作停留，等一场白鸽掠过。" },
    { name: "明孝陵", longitude: 118.839, latitude: 32.061, category: "世界文化遗产", note: "午后走过石象路，把秋色留到光线最柔和的时候。" },
  ],
  京都: [
    { name: "清水寺", longitude: 135.785, latitude: 34.994, category: "古寺 · 山景", note: "从清水舞台俯瞰古都，让晨光先替你打开京都。" },
    { name: "二年坂", longitude: 135.781, latitude: 34.997, category: "町屋 · 慢行", note: "沿石板坡道慢走，在町屋与小店间留出发呆时间。" },
    { name: "祇园", longitude: 135.775, latitude: 35.003, category: "花见小路 · 晚灯", note: "傍晚抵达祇园，等待灯笼与暮色同时亮起。" },
  ],
  大理: [
    { name: "大理古城", longitude: 100.165, latitude: 25.694, category: "古城 · 慢生活", note: "从人民路开始，不设目标地逛进古城的小巷。" },
    { name: "才村码头", longitude: 100.187, latitude: 25.721, category: "洱海 · 骑行", note: "沿生态廊道骑行，把午后的风留给洱海。" },
    { name: "龙龛码头", longitude: 100.199, latitude: 25.672, category: "日落 · 水杉", note: "日落前抵达水边，看光线一点点沉入苍山。" },
  ],
  青岛: [
    { name: "栈桥", longitude: 120.32, latitude: 36.061, category: "海岸 · 地标", note: "清晨沿海岸散步，在城市醒来前先遇见海风。" },
    { name: "大学路", longitude: 120.335, latitude: 36.063, category: "老城 · 街巷", note: "穿过红墙与梧桐，挑一家街角咖啡馆休息。" },
    { name: "小鱼山", longitude: 120.338, latitude: 36.058, category: "登高 · 日落", note: "傍晚登高看红瓦、绿树与海面连成一幅画。" },
  ],
  景德镇: [
    { name: "陶溪川", longitude: 117.228, latitude: 29.291, category: "陶瓷 · 市集", note: "从旧厂房走进年轻市集，遇见正在发生的陶瓷生活。" },
    { name: "御窑博物馆", longitude: 117.205, latitude: 29.294, category: "建筑 · 窑址", note: "在拱券与窑砖间穿行，理解一座城与瓷器的关系。" },
    { name: "三宝村", longitude: 117.263, latitude: 29.246, category: "山谷 · 手作", note: "把下午留给山谷工坊，亲手完成一件旅行纪念。" },
  ],
};

const transitFallbacks: Record<string, Record<string, TransitPlanRow[]>> = {
  南京: {
    "中山陵->音乐台": [
      { kind: "walk", title: "步行接驳", detail: "步行 280 米 到 中山陵南站" },
      { kind: "transit", title: "景区观光车 1 号线", detail: "乘坐 2 站 · 中山陵南站 上车 · 音乐台站 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 120 米 到 音乐台入口" },
    ],
    "音乐台->明孝陵": [
      { kind: "walk", title: "步行接驳", detail: "步行 160 米 到 音乐台站" },
      { kind: "transit", title: "景区观光车 2 号线", detail: "乘坐 1 站 · 音乐台站 上车 · 明孝陵站 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 350 米 到 明孝陵景区入口" },
    ],
  },
  京都: {
    "清水寺->二年坂": [
      { kind: "walk", title: "步行接驳", detail: "步行 200 米 到 清水道站" },
      { kind: "transit", title: "京都市营巴士 206 路", detail: "乘坐 2 站 · 清水道 上车 · 东山安井 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 150 米 到 二年坂" },
    ],
    "二年坂->祇园": [
      { kind: "walk", title: "步行接驳", detail: "步行 260 米 到 东山安井站" },
      { kind: "transit", title: "京都市营巴士 207 路", detail: "乘坐 2 站 · 东山安井 上车 · 祇园 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 180 米 到 花见小路" },
    ],
  },
  大理: {
    "大理古城->才村码头": [
      { kind: "walk", title: "步行接驳", detail: "步行 240 米 到 人民路口站" },
      { kind: "transit", title: "大理旅游专线 C2", detail: "乘坐 4 站 · 人民路口 上车 · 才村码头 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 110 米 到 洱海生态廊道" },
    ],
    "才村码头->龙龛码头": [
      { kind: "walk", title: "步行接驳", detail: "步行 180 米 到 才村码头站" },
      { kind: "transit", title: "环洱海接驳巴士", detail: "乘坐 3 站 · 才村码头 上车 · 龙龛码头 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 90 米 到 龙龛观景点" },
    ],
  },
  青岛: {
    "栈桥->大学路": [
      { kind: "walk", title: "步行接驳", detail: "步行 300 米 到 栈桥站" },
      { kind: "transit", title: "公交 6 路", detail: "乘坐 3 站 · 栈桥站 上车 · 大学路站 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 120 米 到 大学路街角" },
    ],
    "大学路->小鱼山": [
      { kind: "walk", title: "步行接驳", detail: "步行 170 米 到 大学路站" },
      { kind: "transit", title: "公交 214 路", detail: "乘坐 2 站 · 大学路站 上车 · 小鱼山站 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 140 米 到 小鱼山公园" },
    ],
  },
  景德镇: {
    "陶溪川->御窑博物馆": [
      { kind: "walk", title: "步行接驳", detail: "步行 210 米 到 陶溪川北门站" },
      { kind: "transit", title: "公交 1 路", detail: "乘坐 3 站 · 陶溪川北门 上车 · 御窑厂站 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 160 米 到 御窑博物馆" },
    ],
    "御窑博物馆->三宝村": [
      { kind: "walk", title: "步行接驳", detail: "步行 220 米 到 御窑厂站" },
      { kind: "transit", title: "景德镇旅游专线 5 路", detail: "乘坐 5 站 · 御窑厂站 上车 · 三宝村站 下车" },
      { kind: "walk", title: "末段步行", detail: "步行 260 米 到 山谷工坊区" },
    ],
  },
};

function buildCityTrip(city: string): MapData {
  const stops = cityStops[city] || cityStops.南京;
  const makeDay = (day: number, offset: number) => ({
    day,
    city,
    transportation: day === 1 ? "公共交通" : "步行",
    route_ready: true,
    unmapped_points: [],
    points: stops.map((stop, index) => ({
      order: index + 1,
      poi_id: `${city}-${day}-${index + 1}`,
      name: day === 1 ? stop.name : `${stop.name}${offset ? "周边" : ""}`,
      location: { longitude: stop.longitude + offset * 0.006, latitude: stop.latitude + offset * 0.004 },
      start_time: ["09:00", "11:00", "14:00"][index],
      end_time: ["10:30", "12:30", "16:30"][index],
    })),
  });
  return { city, days: [makeDay(1, 0), makeDay(2, 1), makeDay(3, -1)] };
}

const demoData: MapData = buildCityTrip("南京");

type Tab = "ai" | "discover" | "trip" | "profile";
type ChatMessage = {
  role: "user" | "ai";
  text: string;
  tripReady?: boolean;
  streaming?: boolean;
  guideFailed?: boolean;
  /** 热门笔记只作为旅行灵感展示，避免把原文当作用户输入回填到对话框。 */
  trendingNote?: TrendingNote;
  /** 同一篇热门笔记在当前对话内只允许请求一次三路线方案。 */
  trendingPlansRequested?: boolean;
};

const TRENDING_TOPIC_TAGS = new Set(["亲子", "酒店度假", "城市漫游", "自然风光", "旅行攻略", "美食"]);

/** 热门卡片是已选定的旅行灵感；标签和正文足以提供选线所需的最小事实。 */
function buildTrendingInspiration(note: TrendingNote): TrendingInspiration {
  const text = `${note.title} ${note.summary}`;
  const city = note.tags.find((tag) => !TRENDING_TOPIC_TAGS.has(tag)) || "";
  // 笔记通常不会交代游玩天数，因此路线比较统一采用两日基线；选线后仍可调整。
  const daysMatch = text.match(/(?:^|[^第\d])([1-5])\s*(?:天|日)(?:\s*[0-9]+\s*(?:夜|晚))?/);
  const days = daysMatch ? Number(daysMatch[1]) : 2;
  const preferences = note.tags.filter((tag) => TRENDING_TOPIC_TAGS.has(tag)).join(",");
  return {
    id: note.id,
    title: note.title,
    summary: note.summary,
    tags: note.tags,
    tripMeta: { city, days, preferences: preferences || undefined, pace: "normal" },
  };
}

function buildConversationContext(messages: ChatMessage[]): string {
  return messages
    .filter((message) => !message.streaming && message.text.trim())
    .slice(-8)
    .map((message) => `${message.role === "user" ? "用户" : "小渡"}：${message.text.trim().slice(0, 360)}`)
    .join("\n");
}
type ConversationHistoryItem = {
  id: string;
  title: string;
  preview: string;
  updatedAt: string;
  messages: ChatMessage[];
  sessionId: string | null;
  mapData: MapData | null;
};

/** 大师工作流单行状态 */
type StageRow = { agent: string; label: string; status: "start" | "done"; weather?: WeatherBrief[] };

/** 格式化对话时间戳：TODAY HH:MM AM/PM */
function formatChatTime(date: Date): string {
  let h = date.getHours();
  const m = date.getMinutes().toString().padStart(2, "0");
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `TODAY ${h}:${m} ${ampm}`;
}

/** 清理攻略 Markdown 里的 # * --- 等符号，保留 → 和 emoji，让纯文本输出更干净 */
function cleanGuideMarkdown(text: string): string {
  if (!text) return "";
  return text
    .split("\n")
    .filter((line) => line.trim() !== "---" && line.trim() !== "***")  // 去掉 --- 分隔线
    .map((line) => line
      .replace(/^#{1,6}\s*/, "")        // 去掉行首 # 标题
      .replace(/^\s*[-*+]\s+/, "")      // 去掉行首列表符号 - * +
      .replace(/\*\*(.+?)\*\*/g, "$1")  // 去掉 **加粗**
      .replace(/\*(.+?)\*/g, "$1")      // 去掉 *斜体*
      .replace(/^>\s*/, "")              // 去掉行首引用 >
    )
    .join("\n");
}

function conversationTitleFromQuery(query: string): string {
  const compact = query.replace(/\s+/g, " ").trim();
  if (compact.length <= 18) return compact || "新对话";
  return `${compact.slice(0, 18)}…`;
}

function newConversationId(): string {
  return `chat_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 从天气简报提炼穿衣/雨具提醒 */
function buildWeatherTip(briefs: WeatherBrief[]): string {
  if (!briefs.length) return "";
  const first = briefs[0];
  const temps = briefs
    .flatMap((b) => [Number(b.day_temp), Number(b.night_temp)])
    .filter((n) => Number.isFinite(n));
  const hasRain = briefs.some((b) => /雨/.test(`${b.day_weather}${b.night_weather}`));
  const parts: string[] = [];
  if (temps.length) {
    const min = Math.min(...temps);
    const max = Math.max(...temps);
    const range = `${min}~${max}°C`;
    const condText = first.day_weather || "";
    parts.push(`${condText}，${range}`);
    if (max - min >= 8) parts.push("早晚温差大，建议带件外套");
    else if (max <= 15) parts.push("偏凉，注意保暖");
    else if (max >= 32) parts.push("偏热，注意防晒补水");
  }
  parts.push(hasRain ? "有雨，记得带伞☔" : "无需雨具");
  return parts.join("；");
}

function formatDistance(meters?: number): string { if (!meters) return ""; return meters >= 1000 ? `${(meters / 1000).toFixed(1)} 公里` : `${Math.round(meters)} 米`; }
function formatDuration(seconds?: number): string { if (!seconds) return ""; const minutes = Math.round(seconds / 60); return minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟` : `${minutes} 分钟`; }
const ignoreMapPoint = (_point: MapPoint): void => {};

function getTransitPlanRows(leg?: RouteLeg): TransitPlanRow[] {
  if (!leg?.steps?.length) return [];
  const rows: TransitPlanRow[] = [];
  leg.steps.forEach((step) => {
    if (step.kind === "transit") {
      const line = step.lineName || step.instruction;
      const stopText = step.stopCount ? `乘坐 ${step.stopCount} 站` : "查看到站提醒";
      const stationText = step.boardingStop && step.alightingStop ? `${step.boardingStop} 上车 · ${step.alightingStop} 下车` : "查看进出站信息";
      rows.push({ kind: "transit", title: line, detail: `${stopText} · ${stationText}` });
      return;
    }
    if (step.kind === "walk") {
      const detail = [step.distanceMeters ? `步行 ${formatDistance(step.distanceMeters)}` : "步行前往", step.instruction].filter(Boolean).join(" · ");
      rows.push({ kind: "walk", title: "步行接驳", detail });
    }
  });
  return rows;
}

function getTransitMeta(rows: TransitPlanRow[]) {
  const transitCount = rows.filter((row) => row.kind === "transit").length;
  const walkCount = rows.filter((row) => row.kind === "walk").length;
  return {
    transfers: Math.max(0, transitCount - 1),
    walkCount,
    lineSummary: rows.find((row) => row.kind === "transit")?.title || "公共交通方案",
  };
}

function getTransitFallbackRows(city: string, from: string, to: string): TransitPlanRow[] {
  return transitFallbacks[city]?.[`${from}->${to}`] || [
    { kind: "walk", title: "步行接驳", detail: `步行 260 米 到 ${from}附近站点` },
    { kind: "transit", title: "公共交通推荐路线", detail: `${from} 上车 · ${to} 下车 · 乘坐 3 站` },
    { kind: "walk", title: "末段步行", detail: `步行 180 米 到 ${to}` },
  ];
}

function StepRow({ step }: { step: RouteStep }) {
  if (step.kind === "transit") {
    return <li className="tl-node tl-transit"><span className="tl-dot" /><div className="tl-body"><strong>{step.lineName}</strong><small>{step.boardingStop && step.alightingStop ? `${step.boardingStop} 上车 -> ${step.alightingStop} 下车` : ""}{step.stopCount ? ` · 乘坐 ${step.stopCount} 站` : ""}</small></div></li>;
  }
  const meta = [step.distanceMeters ? formatDistance(step.distanceMeters) : "", step.durationSeconds ? formatDuration(step.durationSeconds) : ""].filter(Boolean).join(" · ");
  return <li className={`tl-node tl-${step.kind}`}><span className="tl-dot" /><div className="tl-body"><span className="tl-inst">{step.instruction}</span>{meta && <small>{meta}</small>}</div></li>;
}

function RouteTimeline({ summary, transportation, navActive, onNavigate, onExitNav }: { summary: RouteSummary; transportation: Transportation; navActive: boolean; onNavigate: () => void; onExitNav: () => void }) {
  const [openLegs, setOpenLegs] = useState<Record<number, boolean>>({});
  const legs = summary.legs || [];
  if (summary.status === "loading") return <div className="route-hint">正在规划{transportation}路线...</div>;
  if (!legs.length) return null;
  const meta = [summary.distanceKm ? `约 ${summary.distanceKm.toFixed(1)} 公里` : "", summary.durationMinutes ? `预计 ${formatDuration(summary.durationMinutes * 60)}` : "", summary.tollsYuan ? `过路费 ${summary.tollsYuan} 元` : ""].filter(Boolean).join(" · ");
  if (transportation !== "公共交通") {
    return <div className="route-card"><div className="route-card-head"><div><strong>{`${legs[0].from} -> ${legs[legs.length - 1].to}`}</strong><small>{meta || transportation}</small></div><button className="pill-action" onClick={navActive ? onExitNav : onNavigate}>{navActive ? "退出导航" : "开始导航"}</button></div></div>;
  }
  return <div className="route-card"><div className="route-card-head"><div><strong>{`${legs[0].from} -> ${legs[legs.length - 1].to}`}</strong><small>{meta || transportation}</small></div></div><ol className="timeline"><li className="tl-node tl-start"><span className="tl-dot" /><div className="tl-body"><strong>{legs[0].from}</strong><small>出发</small></div></li>{legs.map((leg) => {
    const open = !!openLegs[leg.index];
    const legMeta = [formatDistance(leg.distanceMeters), formatDuration(leg.durationSeconds)].filter(Boolean).join(" · ");
    return <div key={leg.index} className="tl-group"><li className={`tl-node tl-summary ${open ? "open" : ""}`} onClick={() => setOpenLegs((prev) => ({ ...prev, [leg.index]: !prev[leg.index] }))} role="button" aria-expanded={open}><span className="tl-dot" /><div className="tl-body"><span className="tl-inst">前往{leg.to}<span className="tl-count">（{leg.steps.length} 步）</span></span>{legMeta && <small>{legMeta}</small>}</div><span className={`tl-caret ${open ? "open" : ""}`}>⌄</span></li>{open && leg.steps.map((step, index) => <StepRow key={`${leg.index}-${index}`} step={step} />)}<li className="tl-node tl-waypoint"><span className="tl-dot" /><div className="tl-body"><strong>{leg.to}</strong><small>{leg.index === legs.length - 1 ? "到达" : "途经"}</small></div></li></div>;
  })}</ol></div>;
}

function BottomNav({ active, onChange }: { active: Tab; onChange: (tab: Tab) => void }) {
  const items: { id: Tab; label: string; icon: string }[] = [
    { id: "ai", label: "小渡AI", icon: "✦" },
    { id: "discover", label: "小渡推荐", icon: "⌖" },
    { id: "trip", label: "行程", icon: "◉" },
    { id: "profile", label: "我的", icon: "☻" },
  ];
  return <nav className="bottom-nav">{items.map((item) => <button key={item.id} className={active === item.id ? "active" : ""} onClick={() => onChange(item.id)}><span>{item.icon}</span><small>{item.label}</small></button>)}</nav>;
}

function PhoneHeader({ title, active, onMenu, onProfile }: { title: string; active: Tab; onMenu: () => void; onProfile: () => void }) {
  return <header className="phone-header">{active === "ai" ? <button className="icon-btn wide" onClick={onMenu}>☰</button> : <h1>{title}</h1>}<span className="header-title">{active === "ai" ? "小渡 Ai" : ""}</span><button className="icon-btn" onClick={onProfile}>♙</button></header>;
}

// ─── 对话快填卡 ───────────────────────────────────────────────────
const DAYS_OPTIONS = ["1天", "2天", "3天", "4天", "5天", "7天"];
const PREF_OPTIONS = [
  { label: "美食打卡", icon: "restaurant", cls: "chip-food" },
  { label: "人文历史", icon: "museum", cls: "chip-history" },
  { label: "轻松休闲", icon: "self_improvement", cls: "chip-relax" },
  { label: "亲子游", icon: "family_restroom", cls: "chip-family" },
];
const TRANSPORT_OPTIONS = [
  { label: "自驾", icon: "directions_car" },
  { label: "公共交通", icon: "directions_bus" },
  { label: "高铁", icon: "train" },
  { label: "步行", icon: "directions_walk" },
];
const BUDGET_OPTIONS = [
  { label: "经济", range: "≤1500", amount: 1500 },
  { label: "舒适", range: "1500-4000", amount: 3000 },
  { label: "品质", range: "4000-1万", amount: 8000 },
  { label: "豪华", range: "1万以上", amount: 15000 },
];

function TripFormCard({
  missingFields,
  busy,
  onSubmit,
  onDismiss,
}: {
  missingFields: string[];
  busy: boolean;
  onSubmit: (parts: string[]) => void;
  onDismiss: () => void;
}) {
  // 动态构建步骤：只有 days 缺失时才展示天数步
  const steps = useMemo(() => {
    const s: Array<{ id: string; title: string }> = [];
    if (missingFields.includes("days")) s.push({ id: "days", title: "打算玩几天？" });
    s.push({ id: "preferences", title: "旅行偏好（可多选）" });
    s.push({ id: "transport", title: "出行方式" });
    s.push({ id: "budget", title: "预算档位" });
    return s;
  }, [missingFields]);

  const [step, setStep] = useState(0);
  const [selectedDays, setSelectedDays] = useState<string | null>(null);
  const [selectedPrefs, setSelectedPrefs] = useState<Set<string>>(new Set());
  const [selectedTransport, setSelectedTransport] = useState<string | null>(null);
  const [selectedBudget, setSelectedBudget] = useState<string | null>(null);

  const current = steps[step];
  const isLast = step === steps.length - 1;

  const buildParts = () => {
    const parts: string[] = [];
    if (selectedDays) parts.push(selectedDays);
    if (selectedPrefs.size > 0) parts.push(`偏好${[...selectedPrefs].join("、")}`);
    if (selectedTransport) parts.push(`${selectedTransport}出行`);
    if (selectedBudget) {
      const opt = BUDGET_OPTIONS.find((b) => b.label === selectedBudget);
      if (opt) parts.push(`预算${opt.amount}元左右`);
    }
    return parts;
  };

  const handleNext = () => {
    if (isLast) {
      const parts = buildParts();
      if (parts.length > 0) onSubmit(parts);
      else onDismiss();
    } else {
      setStep((s) => s + 1);
    }
  };

  const handleSkip = () => {
    if (isLast) onDismiss();
    else setStep((s) => s + 1);
  };

  const handleBack = () => {
    if (step > 0) setStep((s) => s - 1);
  };

  return (
    <div className="trip-form-card">
      {/* 顶部：返回 + 步骤点 + 占位 */}
      <div className="trip-form-header">
        <button
          className="trip-form-back"
          onClick={handleBack}
          disabled={step === 0 || busy}
          aria-label="返回上一步"
        >
          <span className="material-symbols-outlined">chevron_left</span>
        </button>
        <div className="trip-form-dots">
          {steps.map((_, i) => (
            <span
              key={i}
              className={`trip-form-dot${i === step ? " active" : i < step ? " done" : ""}`}
            />
          ))}
        </div>
        <span className="trip-form-back-placeholder" />
      </div>

      {/* 标题 */}
      <div className="trip-form-title">{current.title}</div>

      {/* 选项区 2×2 网格 */}
      <div className="trip-form-grid">
        {current.id === "days" &&
          DAYS_OPTIONS.map((d) => (
            <button
              key={d}
              className={`trip-form-option${selectedDays === d ? " selected" : ""}`}
              onClick={() => setSelectedDays((prev) => (prev === d ? null : d))}
              disabled={busy}
            >
              {d}
            </button>
          ))}

        {current.id === "preferences" &&
          PREF_OPTIONS.map((p) => {
            const sel = selectedPrefs.has(p.label);
            return (
              <button
                key={p.label}
                className={`trip-form-option has-icon${sel ? " selected" : ""}`}
                onClick={() =>
                  setSelectedPrefs((prev) => {
                    const next = new Set(prev);
                    sel ? next.delete(p.label) : next.add(p.label);
                    return next;
                  })
                }
                disabled={busy}
              >
                <span className={`material-symbols-outlined ${p.cls}`}>
                  {sel ? "check" : p.icon}
                </span>
                <span>{p.label}</span>
              </button>
            );
          })}

        {current.id === "transport" &&
          TRANSPORT_OPTIONS.map((t) => (
            <button
              key={t.label}
              className={`trip-form-option has-icon${selectedTransport === t.label ? " selected" : ""}`}
              onClick={() => setSelectedTransport((prev) => (prev === t.label ? null : t.label))}
              disabled={busy}
            >
              <span className="material-symbols-outlined">{t.icon}</span>
              <span>{t.label}</span>
            </button>
          ))}

        {current.id === "budget" &&
          BUDGET_OPTIONS.map((b) => (
            <button
              key={b.label}
              className={`trip-form-option has-icon${selectedBudget === b.label ? " selected" : ""}`}
              onClick={() => setSelectedBudget((prev) => (prev === b.label ? null : b.label))}
              disabled={busy}
            >
              <span className="material-symbols-outlined">payments</span>
              <span>{b.label} · {b.range}</span>
            </button>
          ))}
      </div>

      {/* 底部操作 */}
      <div className="trip-form-footer">
        <button className="trip-form-skip" onClick={handleSkip} disabled={busy}>
          跳过
        </button>
        <button className="trip-form-next" onClick={handleNext} disabled={busy}>
          {isLast ? "开始规划" : "下一步"}
          <span className="material-symbols-outlined">
            {isLast ? "rocket_launch" : "chevron_right"}
          </span>
        </button>
      </div>
    </div>
  );
}

// 7:210 对话中页面（严格按 Stitch 新稿 (9) code.html 1:1 还原）
function GuideRouteOptionsCard({ options, selectedRouteId, onSelect, onRegenerate }: { options: RouteOption[]; selectedRouteId?: string | null; onSelect: (route: RouteOption) => void; onRegenerate: () => void }) {
  if (!options.length) return null;
  return <div className="guide-route-card">
    <div className="guide-route-head">
      <div>
        <span>ROUTE OPTIONS</span>
        <h3>主人请选择路线</h3>
      </div>
      <button className="guide-route-refresh" onClick={onRegenerate}>重新生成</button>
    </div>
    <div className="guide-route-list">
      {options.map((option, index) => (
        <button
          key={option.id || index}
          className={`guide-route-option ${selectedRouteId === option.id ? "selected" : ""}`}
          onClick={() => onSelect(option)}
        >
          <div className="guide-route-option-title">{option.title || `路线 ${index + 1}`}</div>
          {option.route_text && <div className="guide-route-option-path">{option.route_text}</div>}
        </button>
      ))}
    </div>
  </div>;
}

function ChatView({ title, messages, input, busy, statusText, showFormCard, missingFields, agentStages, routeOptions, selectedRouteId, onRouteSelect, onRegenerateGuide, onGenerateTrendingPlans, onEditMessage, onRegenerateMessage, onFormSubmit, onFormDismiss, onInput, onSubmit, onStop, onViewTrip, onMenu, onHistory, onNavChange, active }: { title: string; messages: ChatMessage[]; input: string; busy: boolean; statusText: string; showFormCard: boolean; missingFields: string[]; agentStages: StageRow[]; routeOptions: RouteOption[]; selectedRouteId?: string | null; onRouteSelect: (route: RouteOption) => void; onRegenerateGuide: () => void; onGenerateTrendingPlans: (note: TrendingNote) => void; onEditMessage: (index: number, text: string) => void; onRegenerateMessage: (index: number) => void; onFormSubmit: (parts: string[]) => void; onFormDismiss: () => void; onInput: (value: string) => void; onSubmit: (event: FormEvent) => void; onStop: () => void; onViewTrip: () => void; onMenu: () => void; onHistory: () => void; onNavChange: (tab: Tab) => void; active: Tab }) {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  // (PREF_OPTIONS already used by TripFormCard above)
  const hasText = input.trim().length > 0;
  const chatAreaRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = chatAreaRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy, statusText, showFormCard, agentStages, routeOptions]);
  return <>
    <section className="ai-chat-screen">
      <main className="ai-chat-main">
        <div className="ai-chat-mist">
          <div className="mist-orb a" />
          <div className="mist-orb b" />
        </div>
        <div className="ai-chat-area" ref={chatAreaRef}>
          <div className="ai-chat-time">{formatChatTime(new Date())}</div>
          {messages.map((message, index) => {
            // 阶段 A：阶段条紧跟最后一条 user 消息（在攻略文案之前）
            const isLastUser = message.role === "user" && index === messages.length - 1 - [...messages].reverse().findIndex((m) => m.role === "user");
            const isPhaseB = agentStages.some((s) => ["route_confirm", "attraction", "weather", "hotel", "planner"].includes(s.agent));
            return <Fragment key={index}>
              {message.role === "user"
                ? <div className="ai-chat-msg user">
                    <div className="ai-chat-bubble-user">{message.text}</div>
                  </div>
                : <div className="ai-chat-ai-row">
                    <div className="ai-chat-ai-head">
                      <div className="ai-chat-ai-avatar"><img src={homeFan.aiAvatar} alt="AI Avatar" /></div>
                      <span className="ai-chat-ai-name">小渡 AI</span>
                    </div>
                    <div className="ai-chat-ai-text">{cleanGuideMarkdown(message.text)}</div>
                    {message.trendingNote && (
                      <div className="trending-plan-preview">
                        {message.trendingNote.cover_url && <img src={message.trendingNote.cover_url} alt="" />}
                        <div>
                          <span className="trending-plan-preview-eyebrow">旅行灵感已选中</span>
                          <strong>{message.trendingNote.title}</strong>
                          <div className="trending-tags">{message.trendingNote.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
                          <p>我会先按经典必去、轻松慢游、特色体验整理 3 个路线方向。选中后再生成完整行程。</p>
                          <button disabled={busy || message.trendingPlansRequested} onClick={() => onGenerateTrendingPlans(message.trendingNote!)}>
                            <span className="material-symbols-outlined">route</span>
                            {message.trendingPlansRequested ? "路线方案已生成" : "查看 3 个路线方案"}
                          </button>
                        </div>
                      </div>
                    )}
                    {message.tripReady && (
                      <div className="ai-chat-trip-actions">
                        <button className="ai-chat-trip-btn primary" onClick={onViewTrip}>
                          <span className="material-symbols-outlined">map</span>
                          查看完整行程
                        </button>
                        <button className="ai-chat-trip-btn ghost" onClick={() => chatAreaRef.current?.blur()}>
                          <span className="material-symbols-outlined">edit_note</span>
                          再调整一下
                        </button>
                      </div>
                    )}
                    {message.guideFailed && !busy && (
                      <div className="ai-chat-trip-actions">
                        <button className="ai-chat-trip-btn primary" onClick={onRegenerateGuide}>
                          <span className="material-symbols-outlined">refresh</span>
                          重新生成攻略
                        </button>
                      </div>
                    )}
                    {!message.streaming && !message.tripReady && !message.trendingNote && (
                      <div className="ai-chat-msg-actions">
                        <button className="ai-chat-action-btn" onClick={() => navigator.clipboard?.writeText(message.text)}>
                          <span className="material-symbols-outlined">content_copy</span>
                          复制
                        </button>
                        <button className="ai-chat-action-btn" onClick={() => onEditMessage(index, message.text)}>
                          <span className="material-symbols-outlined">edit</span>
                          编辑
                        </button>
                        <button className="ai-chat-action-btn" onClick={() => onRegenerateMessage(index)} disabled={busy}>
                          <span className="material-symbols-outlined">refresh</span>
                          重新生成
                        </button>
                      </div>
                    )}
                  </div>}
              {/* 阶段 A 进度卡：紧跟最后一条 user 消息，在攻略文案之前 */}
              {isLastUser && busy && agentStages.length > 0 && !isPhaseB && (
                <div className="agent-stage-card">
                  {agentStages.map((s) => (
                    <div className={`agent-stage-row ${s.status}`} key={s.agent}>
                      <span className={`agent-stage-icon material-symbols-outlined`}>
                        {s.status === "done" ? "check_circle" : "progress_activity"}
                      </span>
                      <div className="agent-stage-text">
                        <span className="agent-stage-label">{s.label}</span>
                        {s.status === "start" && s.agent === "knowledge" && <span className="agent-stage-sub">小红书笔记搜索🔍中....</span>}
                        {s.status === "start" && s.agent === "guide" && <span className="agent-stage-sub">正在整理路线方案....</span>}
                        {s.status === "start" && s.agent === "intent" && <span className="agent-stage-sub">正在理解你的需求....</span>}
                        {s.status === "start" && !["knowledge","guide","intent"].includes(s.agent) && <span className="agent-stage-doing">工作中…</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Fragment>;
          })}
          {/* 阶段 B 进度卡：在所有消息之后（「已选择」AI 消息之后） */}
          {busy && agentStages.length > 0 && agentStages.some((s) => ["route_confirm", "attraction", "weather", "hotel", "planner"].includes(s.agent)) && (
            <div className="agent-stage-card">
              {agentStages.map((s) => (
                <div className={`agent-stage-row ${s.status}`} key={s.agent}>
                  <span className={`agent-stage-icon material-symbols-outlined`}>
                    {s.status === "done" ? "check_circle" : "progress_activity"}
                  </span>
                  <div className="agent-stage-text">
                    <span className="agent-stage-label">{s.label}</span>
                    {s.status === "start" && s.agent === "attraction" && <span className="agent-stage-sub">高德搜索真实景点....</span>}
                    {s.status === "start" && s.agent === "weather" && <span className="agent-stage-sub">查询实时天气....</span>}
                    {s.status === "start" && s.agent === "hotel" && <span className="agent-stage-sub">匹配附近酒店....</span>}
                    {s.status === "start" && s.agent === "planner" && <span className="agent-stage-sub">整合确认卡....</span>}
                    {s.status === "start" && !["attraction","weather","hotel","planner"].includes(s.agent) && <span className="agent-stage-doing">工作中…</span>}
                    {s.status === "done" && s.weather && s.weather.length > 0 && (
                      <span className="agent-stage-weather">
                        ☀️ {s.weather[0].day_weather || ""} {s.weather[0].day_temp}°/{s.weather[0].night_temp}°
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          {routeOptions.length > 0 && !showFormCard && (
            <GuideRouteOptionsCard
              options={routeOptions}
              selectedRouteId={selectedRouteId}
              onSelect={onRouteSelect}
              onRegenerate={onRegenerateGuide}
            />
          )}
          {showFormCard && !busy && (
            <TripFormCard
              missingFields={missingFields}
              busy={busy}
              onSubmit={onFormSubmit}
              onDismiss={onFormDismiss}
            />
          )}
          {busy && (
            <div className="ai-chat-typing show">
              <div className="tt-avatar"><img src={homeFan.aiAvatar} alt="AI Avatar" /></div>
              <div className="tt-dots"><span /><span /><span /></div>
            </div>
          )}
          {statusText && <div className="ai-chat-time">{statusText}</div>}
        </div>
      </main>
    </section>

    <header className="ai-home-header">
      <div className="ah-row ah-row-chat">
        <button className="ah-icon-btn" onClick={onMenu} aria-label="menu">
          <span className="material-symbols-outlined">menu</span>
        </button>
        <h1 className="ah-chat-title">
          <img src={homeFan.chatLogo} alt="Logo" className="ah-chat-logo" />
          <span>{title || "小渡 Ai"}</span>
        </h1>
        <button className="ah-icon-btn" onClick={onHistory} aria-label="history">
          <span className="material-symbols-outlined">history</span>
        </button>
      </div>
    </header>

    <div className="ai-chat-input">
      <div className="input-card">
        <form className="input-row" onSubmit={onSubmit}>
          <button type="button" className="btn-add" aria-label="add"><span className="material-symbols-outlined">add</span></button>
          <input value={input} onChange={(event) => onInput(event.target.value)} placeholder="Ask a task or anything?" type="text" />
          <button type="button" className={`btn-voice btn-stop ${busy || !hasText ? "no-text" : "has-text"}`} onClick={onStop} disabled={!busy} aria-label="停止生成" title="停止生成"><span className="stop-square" /></button>
          <button type="submit" className={`btn-send ${hasText && !busy ? "has-text" : "no-text"}`} disabled={busy || !hasText} aria-label="send"><span className="material-symbols-outlined">send</span></button>
        </form>
      </div>
    </div>

    <nav className="ai-home-dock" aria-label="main nav">
      <div className="dock-inner">
        {[
          { id: "ai" as Tab, label: "小渡AI", img: homeFan.navAiNew },
          { id: "discover" as Tab, label: "小渡推荐", icon: "lightbulb" },
          { id: "trip" as Tab, label: "行程", icon: "explore" },
          { id: "profile" as Tab, label: "我的", icon: "account_circle" },
        ].map((item) => (
          <button key={item.id} className={active === item.id ? "active" : ""} onClick={() => onNavChange(item.id)}>
            {item.img ? <img src={item.img} alt={item.label} /> : <span className="material-symbols-outlined">{item.icon}</span>}
            <small>{item.label}</small>
          </button>
        ))}
      </div>
    </nav>
  </>;
}

// 1:535 小渡 AI 首页（严格按 Stitch code.html 1:1 还原）
function AIDiscoverHomeView({ onSend, onProfile, onMenu, active, onNavChange, isNewChat }: { onSend: (query: string) => void; onProfile: () => void; onMenu: () => void; active: Tab; onNavChange: (tab: Tab) => void; isNewChat: boolean }) {
  const [draft, setDraft] = useState("");
  const submit = (event: FormEvent) => { event.preventDefault(); const v = draft.trim(); if (v) { onSend(v); setDraft(""); } };
  const navItems: { id: Tab; label: string; icon: string; img?: string }[] = [
    { id: "ai", label: "小渡AI", icon: "auto_awesome", img: homeFan.navAi },
    { id: "discover", label: "小渡推荐", icon: "lightbulb" },
    { id: "trip", label: "行程", icon: "explore" },
    { id: "profile", label: "我的", icon: "account_circle" },
  ];
  return <>
    <section className="ai-home-screen">
      <main className="ai-home-main">
        <div className="ai-home-mist">
          <div className="mist-orb a" />
          <div className="mist-orb b" />
        </div>

        <div className="ai-home-fan">
          <div className="fan-card c1"><div className="fan-img" style={{ backgroundImage: `url("${homeFan.c1}")` }} /></div>
          <div className="fan-card c2"><div className="fan-img" style={{ backgroundImage: `url("${homeFan.c2}")` }} /></div>
          <div className="fan-card c3"><div className="fan-img" style={{ backgroundImage: `url("${homeFan.c3}")` }} /></div>
          <div className="fan-card c4"><div className="fan-img" style={{ backgroundImage: `url("${homeFan.c4}")` }} /></div>
          <div className="fan-card c5"><div className="fan-img" style={{ backgroundImage: `url("${homeFan.c5}")` }} /></div>
        </div>

        <div className="ai-home-copy">
          <h2>
            <span className="sub">不知道怎么做攻略</span>
            小渡给你推荐
          </h2>
          <div className="hint"><span className="material-symbols-outlined">graphic_eq</span>您请说...</div>
        </div>

        <div className="ai-home-input">
          <div className="input-card">
            {!draft && <div className="input-hint">
              <span className="material-symbols-outlined">auto_awesome</span>
              <p>根据目的地和您的偏好进行个性化推荐...</p>
            </div>}
            <form className="input-row" onSubmit={submit}>
              <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="想去外星" type="text" />
              <button type="button" className="btn-add" aria-label="add"><span className="material-symbols-outlined">add</span></button>
              <button type="button" className="btn-voice btn-stop" disabled aria-label="停止生成" title="当前没有正在生成的回复"><span className="stop-square" /></button>
              <button type="submit" className="btn-send" aria-label="send"><span className="material-symbols-outlined">send</span></button>
            </form>
          </div>
        </div>
      </main>
    </section>

    <header className="ai-home-header">
      <div className="ah-row">
        <div className="ah-brand">
          <button className="icon-btn wide" onClick={onMenu} aria-label="menu" style={{ marginRight: 4 }}><span className="material-symbols-outlined">menu</span></button>
          {isNewChat
            ? <h1 className="ah-new-chat-title">新对话</h1>
            : <><img src={homeFan.logo} alt="Logo" className="ah-logo" /><h1>小渡 Ai</h1></>}
        </div>
        <button className="ah-avatar" onClick={onProfile} aria-label="profile"><span className="material-symbols-outlined">person</span></button>
      </div>
    </header>

    <nav className="ai-home-dock" aria-label="main nav">
      <div className="dock-inner">
        {navItems.map((item) => (
          <button key={item.id} className={active === item.id ? "active" : ""} onClick={() => onNavChange(item.id)}>
            {item.img ? <img src={item.img} alt={item.label} /> : <span className="material-symbols-outlined">{item.icon}</span>}
            <small>{item.label}</small>
          </button>
        ))}
      </div>
    </nav>
  </>;
}

// 小渡推荐：Stitch (14) 卡背 → Stitch (13) 卡面的旅行灵感抽卡
function DiscoverView({ onStart, onSearch, onProfile, active, onNavChange, onGoTrip, personalized }: { onStart: (note: TrendingNote) => void; onSearch: (query: string) => void; onProfile: () => void; active: Tab; onNavChange: (tab: Tab) => void; onGoTrip: (seed: TripSeed) => void; personalized: boolean }) {
  const [query, setQuery] = useState("");
  const [oracleState, setOracleState] = useState<"idle" | "drawing" | "revealed">("idle");
  const [destinationIndex, setDestinationIndex] = useState(0);
  const timersRef = useRef<number[]>([]);
  const [trendingNotes, setTrendingNotes] = useState<TrendingNote[]>([]);
  const [trendingLoading, setTrendingLoading] = useState(true);
  const topics = ["亲子游", "美食打卡", "少走路", "周末逃跑计划"];
  const destinations = [
    { city: "京都", english: "KYOTO", theme: "晚秋 · 慢行 · 寺院", note: "循着红叶与晚钟，走进一场安静的古都漫游。", query: "京都三天两夜晚秋慢行寺院之旅" },
    { city: "大理", english: "DALI", theme: "山海 · 发呆 · 风", note: "把日程交给苍山和洱海，慢一点也很好。", query: "大理三天两夜山海慢游行程" },
    { city: "青岛", english: "QINGDAO", theme: "海岸 · 老城 · 微醺", note: "沿着红瓦海岸散步，收藏一段清爽的城市假期。", query: "青岛周末海岸老城旅行行程" },
    { city: "景德镇", english: "JINGDEZHEN", theme: "陶瓷 · 市集 · 手作", note: "在窑火与山雾之间，遇见一座可以触摸的城市。", query: "景德镇陶瓷市集手作旅行行程" },
  ];
  const destination = destinations[destinationIndex];

  useEffect(() => {
    [homeFan.oracleBack, homeFan.oracleFront].forEach((src) => { const image = new Image(); image.src = src; });
    return () => timersRef.current.forEach((timer) => window.clearTimeout(timer));
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void fetchTrendingNotes(controller.signal, personalized)
      .then(({ notes }) => setTrendingNotes(notes || []))
      .catch(() => setTrendingNotes([]))
      .finally(() => setTrendingLoading(false));
    return () => controller.abort();
  }, [personalized]);

  const drawDestination = () => {
    if (oracleState === "drawing") return;
    timersRef.current.forEach((timer) => window.clearTimeout(timer));
    timersRef.current = [];
    setDestinationIndex((current) => (current + 1 + Math.floor(Math.random() * (destinations.length - 1))) % destinations.length);
    if (oracleState === "revealed") {
      setOracleState("idle");
      timersRef.current.push(window.setTimeout(() => setOracleState("drawing"), 100));
      timersRef.current.push(window.setTimeout(() => setOracleState("revealed"), 720));
      return;
    }
    setOracleState("drawing");
    timersRef.current.push(window.setTimeout(() => setOracleState("revealed"), 620));
  };

  const submitSearch = () => {
    onSearch(query || "南京三天两夜，少走路，想看秋景");
    setQuery("");
  };

  return <>
    <section className="discover-screen-stitch">
      <main className="discover-main">
        <div className="discover-mist" aria-hidden="true"><span /><span /></div>
        <div className="discover-search">
          <span className="material-symbols-outlined">search</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入目的地，或者交给命运…" onKeyDown={(event) => { if (event.key === "Enter") submitSearch(); }} />
          <button className="discover-send" onClick={submitSearch} aria-label="发送旅行需求"><span className="material-symbols-outlined">arrow_forward</span></button>
        </div>

        <div className="discover-topics" aria-label="旅行偏好">
          {topics.map((topic) => <button key={topic} onClick={() => onSearch(`我想要${topic}旅行推荐`)}>{topic}</button>)}
        </div>

        <section className={`oracle-shell ${oracleState}`} aria-labelledby="oracle-title">
          <div className="oracle-heading">
            <div><span className="oracle-kicker">DESTINATION ORACLE</span><h2 id="oracle-title">今天，让命运替你选个地方</h2></div>
            <span className="oracle-hint">{oracleState === "revealed" ? "灵感已揭晓" : "轻触卡片抽取"}</span>
          </div>

          <div className="oracle-stage" role="button" tabIndex={oracleState === "drawing" ? -1 : 0} aria-label={oracleState === "revealed" ? `已抽到${destination.city}，点击再抽一次` : "抽取旅行目的地"} aria-disabled={oracleState === "drawing"} onClick={drawDestination} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); drawDestination(); } }}>
            <div className="oracle-orbit" aria-hidden="true"><i /><i /><i /></div>
            <div className="oracle-deck-card left" aria-hidden="true"><img src={homeFan.oracleBack} alt="" /></div>
            <div className="oracle-deck-card right" aria-hidden="true"><img src={homeFan.oracleBack} alt="" /></div>
            <div className="oracle-card-wrap">
              <div className="oracle-card">
                <div className="oracle-face oracle-back"><img src={homeFan.oracleBack} alt="旅行灵感卡背" /></div>
                <div className="oracle-face oracle-front">
                  <img src={homeFan.oracleFront} alt="旅行灵感卡面" />
                  <div className="oracle-result">
                    <span>{destination.english}</span>
                    <strong>{destination.city}</strong>
                    <small>{destination.theme}</small>
                  </div>
                  <div className="oracle-shine" aria-hidden="true" />
                </div>
              </div>
            </div>
          </div>

          <p className="oracle-note" aria-live="polite">{oracleState === "revealed" ? destination.note : oracleState === "drawing" ? "正在感应与你此刻最合拍的远方…" : "无需计划周全，先从一次心动开始。"}</p>
          <div className="oracle-actions">
            <button className="oracle-primary" onClick={drawDestination} disabled={oracleState === "drawing"}>
              <span className="material-symbols-outlined">{oracleState === "revealed" ? "refresh" : "auto_awesome"}</span>
              {oracleState === "drawing" ? "正在感应目的地" : oracleState === "revealed" ? "再抽一次" : "立即抽卡"}
            </button>
            {oracleState === "revealed" && <button className="oracle-secondary" onClick={() => onGoTrip({ city: destination.city, query: destination.query, theme: destination.theme })}><span>生成这个行程</span><span className="material-symbols-outlined">arrow_forward</span></button>}
          </div>
        </section>

        <section className="discover-content" aria-labelledby="popular-title">
          <div className="discover-section-head"><div><span>DAILY AT 18:00</span><h2 id="popular-title">热门推荐</h2></div></div>
          {trendingLoading && <p className="trending-empty">正在加载近期旅行热帖…</p>}
          {!trendingLoading && !trendingNotes.length && <p className="trending-empty">今日热帖正在整理中，稍后再来看看。</p>}
          <div className="trending-notes">{trendingNotes.map((note) => <article className="trending-note" key={note.id}>
            {note.cover_url ? <img src={note.cover_url} alt="" className="trending-note-cover" /> : <div className="trending-note-cover placeholder"><span className="material-symbols-outlined">travel_explore</span></div>}
            <div className="trending-note-body"><h3>{note.title}</h3><div className="trending-tags">{(note.tags || []).map((tag) => <span key={tag}>{tag}</span>)}</div><p>{note.summary || "点击去小红书查看这篇旅行笔记。"}</p><div className="trending-note-actions"><a href={note.note_url} target="_blank" rel="noreferrer">查看完整笔记 <span className="material-symbols-outlined">open_in_new</span></a><button className="trending-plan" onClick={() => onStart(note)}>规划我的行程</button></div></div>
          </article>)}</div>
        </section>
      </main>
    </section>

    <header className="ai-home-header">
      <div className="ah-row ah-row-discover">
        <div className="ah-discover-brand"><img src={homeFan.logo} alt="小渡" /><h1 className="ah-discover-title">小渡推荐</h1></div>
        <button className="ah-discover-avatar" onClick={onProfile} aria-label="个人中心"><span className="material-symbols-outlined">person</span></button>
      </div>
    </header>

    <nav className="ai-home-dock" aria-label="main nav">
      <div className="dock-inner">
        {[
          { id: "ai" as Tab, label: "小渡AI", img: homeFan.navAiInspire },
          { id: "discover" as Tab, label: "小渡推荐", icon: "lightbulb" },
          { id: "trip" as Tab, label: "行程", icon: "explore" },
          { id: "profile" as Tab, label: "我的", icon: "account_circle" },
        ].map((item) => <button key={item.id} className={active === item.id ? "active" : ""} onClick={() => onNavChange(item.id)}>{item.img ? <img src={item.img} alt={item.label} /> : <span className="material-symbols-outlined">{item.icon}</span>}<small>{item.label}</small></button>)}
      </div>
    </nav>
  </>;
}

function getStopMeta(city: string, point: MapPoint, index: number) {
  const stop = (cityStops[city] || cityStops.南京).find((item) => point.name.startsWith(item.name));
  const images = [homeFan.tripZhongshan, homeFan.tripMusic, homeFan.tripMing];
  return {
    category: stop?.category || "城市精选地点",
    note: stop?.note || "跟随小渡安排的节奏，慢慢感受这座城市。",
    image: images[index % images.length],
  };
}

function TripDock({ onChange }: { onChange: (tab: Tab) => void }) {
  return <nav className="ai-home-dock trip-dock" aria-label="main nav"><div className="dock-inner">{[
    { id: "ai" as Tab, label: "小渡AI", img: homeFan.navAiNew },
    { id: "discover" as Tab, label: "小渡推荐", icon: "lightbulb" },
    { id: "trip" as Tab, label: "行程", icon: "explore" },
    { id: "profile" as Tab, label: "我的", icon: "account_circle" },
  ].map((item) => <button key={item.id} className={item.id === "trip" ? "active" : ""} onClick={() => onChange(item.id)}>{item.img ? <img src={item.img} alt={item.label} /> : <span className="material-symbols-outlined">{item.icon}</span>}<small>{item.label}</small></button>)}</div></nav>;
}

function continueInAmap(from: MapPoint | undefined, to: MapPoint | undefined, transportation: Transportation) {
  if (!to) return;
  const fromLocation = from?.location;
  const toLocation = to.location;
  const hasCoordinates = fromLocation && toLocation
    && Number.isFinite(fromLocation.longitude) && Number.isFinite(fromLocation.latitude)
    && Number.isFinite(toLocation.longitude) && Number.isFinite(toLocation.latitude);
  const mode = transportation === "公共交通" ? "transit" : transportation === "驾车" ? "car" : transportation === "骑行" ? "ride" : "walk";
  const url = hasCoordinates
    ? `https://uri.amap.com/navigation?from=${fromLocation!.longitude},${fromLocation!.latitude},${encodeURIComponent(from?.name || "起点")}&to=${toLocation.longitude},${toLocation.latitude},${encodeURIComponent(to.name)}&mode=${mode}&coordinate=gaode&callnative=1`
    : `https://www.amap.com/search?query=${encodeURIComponent(to.name)}`;
  window.open(url, "_blank", "noopener,noreferrer");
}

function TripView({ data, activeDay, transportation, summary, subView, selectedPoint, transitOpen, saved, adjustExpanded, adjustText, adjustMessages, adjustBusy, onToggleSave, onAdjustCollapse, onAdjustStop, onAdjustText, onAdjustSubmit, onDay, onMode, onSummary, onSelectPoint, onOpenDetail, onBackOverview, onOpenTransit, onCloseTransit, onNavigate, onExitNav, onNavChange, mapRef }: { data: MapData | null; activeDay: number; transportation: Transportation; summary: RouteSummary; subView: TripSubView; selectedPoint: number; transitOpen: boolean; saved: boolean; adjustExpanded: boolean; adjustText: string; adjustMessages: ChatMessage[]; adjustBusy: boolean; onToggleSave: () => void; onAdjustCollapse: () => void; onAdjustStop: () => void; onAdjustText: (text: string) => void; onAdjustSubmit: (text: string) => void; onDay: (index: number) => void; onMode: (mode: Transportation) => void; onSummary: (summary: RouteSummary) => void; onSelectPoint: (point: MapPoint) => void; onOpenDetail: () => void; onBackOverview: () => void; onOpenTransit: () => void; onCloseTransit: () => void; onNavigate: () => void; onExitNav: () => void; onNavChange: (tab: Tab) => void; mapRef: React.RefObject<RouteMapHandle | null> }) {
  if (!data) {
    return <section className="trip-empty-screen">
      <div className="trip-empty-orbit"><span className="material-symbols-outlined">explore</span></div>
      <small>MY JOURNEY</small>
      <h1>还没有行程</h1>
      <p>从小渡 AI 或热门旅行灵感开始，选好路线后你的行程会出现在这里。</p>
      <button type="button" onClick={() => onNavChange("ai")}><span className="material-symbols-outlined">auto_awesome</span>去规划行程</button>
      <TripDock onChange={onNavChange} />
    </section>;
  }
  const day = data.days[activeDay] || data.days[0];
  const point = day.points[Math.min(selectedPoint, Math.max(day.points.length - 1, 0))] || day.points[0];
  const nextPoint = day.points[Math.min(selectedPoint + 1, Math.max(day.points.length - 1, 0))] || point;
  const selectedLeg = summary.legs?.find((leg) => leg.index === selectedPoint) || summary.legs?.[0];
  const duration = selectedLeg?.durationSeconds ? formatDuration(selectedLeg.durationSeconds) : summary.durationMinutes ? `${summary.durationMinutes} 分钟` : "约 25 分钟";
  const distance = selectedLeg?.distanceMeters ? formatDistance(selectedLeg.distanceMeters) : summary.distanceKm ? `${summary.distanceKm.toFixed(1)} 公里` : "3.8 公里";
  const transitRowsBase = getTransitPlanRows(selectedLeg);
  const transitRows = transitRowsBase.length ? transitRowsBase : getTransitFallbackRows(data.city, point?.name || "起点", nextPoint?.name || "终点");
  const transitMeta = getTransitMeta(transitRows);
  const transitSummary = `${transitMeta.lineSummary} · ${duration}${transitMeta.transfers ? ` · ${transitMeta.transfers}次换乘` : " · 直达优先"}`;

  if (subView === "detail") {
    return <section className="trip-detail-screen">
      <header className="trip-sub-header"><button onClick={onBackOverview} aria-label="返回行程总览"><span className="material-symbols-outlined">arrow_back</span></button><h1>详细行程</h1></header>
      <main className="trip-detail-main">
        <div className="trip-detail-title"><h2>今日行程: {data.city}</h2><p><span className="material-symbols-outlined">calendar_today</span>10月{24 + activeDay}日, 周{["四", "五", "六"][activeDay] || "日"}</p></div>
        <div className="trip-day-timeline">{day.points.map((item, index) => {
          const meta = getStopMeta(data.city, item, index);
          const expanded = index === selectedPoint;
          const following = day.points[index + 1];
          return <div className="trip-time-block" key={item.poi_id}>
            <button className={`trip-time-place ${expanded ? "expanded" : ""}`} onClick={() => onSelectPoint(item)} aria-expanded={expanded}>
              <div className="trip-time-rail"><span className={index === 0 ? "live" : ""} /><time>{item.start_time || `${9 + index * 2}:00`}</time></div>
              <article className="trip-place-card">
                <div className="trip-place-copy"><div><h3>{item.name}</h3><p>{meta.category}</p></div>{index === 0 && <span className="trip-rating"><span className="material-symbols-outlined">star</span>4.8</span>}</div>
                {expanded && <><img src={meta.image} alt={`${item.name}旅行风景`} /><p className="trip-place-note">{meta.note}</p><div className="trip-place-actions"><span><span className="material-symbols-outlined">info</span>查看详情</span><span className="material-symbols-outlined">more_horiz</span></div></>}
                {!expanded && <div className="trip-compact-action">点击展开 <span className="material-symbols-outlined">expand_more</span></div>}
              </article>
            </button>
            {following && (transportation === "公共交通"
              ? <button className="trip-transfer transit" onClick={onOpenTransit}><span className="trip-transfer-icon material-symbols-outlined">tram</span><span><strong>公共交通前往</strong><small>{duration} · {transitMeta.transfers ? `${transitMeta.transfers}次换乘` : "换乘友好"}</small></span><span className="trip-transfer-cta">查看站点路线</span></button>
              : <button className="trip-transfer" onClick={onNavigate}><span className="trip-transfer-icon material-symbols-outlined">{transportation === "驾车" ? "directions_car" : transportation === "骑行" ? "directions_bike" : "directions_walk"}</span><span><strong>{transportation === "驾车" ? "驾车前往" : transportation === "骑行" ? "骑行前往" : "步行前往"}</strong><small>{duration} · {distance}</small></span><span className="material-symbols-outlined">arrow_forward</span></button>)}
          </div>;
        })}</div>
      </main>
    </section>;
  }

  if (subView === "navigating") {
    return <section className="trip-navigation-screen">
      <div className="trip-nav-map"><RouteMap ref={mapRef} day={day} transportation={transportation} onSelectPoint={onSelectPoint} onSummaryChange={onSummary} /></div>
      <header className="trip-nav-header"><button onClick={onExitNav} aria-label="结束导航"><span className="material-symbols-outlined">close</span></button><div><small>正在前往</small><strong>{nextPoint?.name || point?.name}</strong></div><span className="trip-nav-live">导航中</span></header>
      <div className="trip-nav-instruction"><span className="material-symbols-outlined">turn_right</span><div><strong>沿当前路线继续前行</strong><small>{duration} · {distance}</small></div></div>
      <button className="trip-nav-ai" aria-label="询问小渡"><span className="material-symbols-outlined">auto_awesome</span></button>
      <button className="trip-end-nav" onClick={onExitNav}>结束导航</button>
    </section>;
  }

  return <section className="trip-overview-screen">
    <header className="trip-overview-header"><div><small>MY JOURNEY</small><h1>{data.city} · {data.days.length}天{Math.max(data.days.length - 1, 1)}晚</h1></div><div className="trip-overview-header-actions"><button className={`trip-save-btn ${saved ? "saved" : ""}`} onClick={onToggleSave} aria-label={saved ? "取消收藏" : "收藏行程"} aria-pressed={saved}><span className="material-symbols-outlined">{saved ? "bookmark_added" : "bookmark"}</span></button><button onClick={() => onNavChange("profile")} aria-label="个人中心"><span className="material-symbols-outlined">person</span></button></div></header>
    <div className="trip-map-layer"><RouteMap ref={mapRef} day={day} transportation={transportation} onSelectPoint={onSelectPoint} onSummaryChange={onSummary} /></div>
    <div className="trip-date-capsule"><div><strong>{data.city} · 第 {day.day} 天</strong><small>{day.points.length} 个地点 · 10月{24 + activeDay}日</small></div><button aria-label="调整行程"><span className="material-symbols-outlined">tune</span></button><div className="trip-date-tabs">{data.days.map((item, index) => <button key={item.day} className={index === activeDay ? "active" : ""} onClick={() => onDay(index)}>D{item.day}</button>)}</div></div>
    <div className={`trip-route-sheet ${transitOpen ? "transit-open" : ""}`}>
      <div className="trip-sheet-handle" />
      <div className="trip-route-heading"><span className="trip-mode-icon material-symbols-outlined">{transportation === "驾车" ? "directions_car" : transportation === "步行" ? "directions_walk" : transportation === "骑行" ? "directions_bike" : "directions_bus"}</span><div><h2>{point?.name || "起点"}<span className="material-symbols-outlined">arrow_forward</span>{nextPoint?.name || "终点"}</h2><p>{transportation === "公共交通" ? transitSummary : `推荐路线 · ${summary.status === "loading" ? "正在规划" : "交通顺畅"}`}</p></div><button aria-label="更多路线选项"><span className="material-symbols-outlined">more_vert</span></button></div>
      <div className="trip-mode-tabs">{modes.map((mode) => <button key={mode} className={transportation === mode ? "active" : ""} onClick={() => onMode(mode)}>{mode}</button>)}</div>
      {transportation === "公共交通"
        ? <>
            <div className="trip-transit-summary"><div><small>步行接驳</small><strong>{transitMeta.walkCount} 段</strong></div><i /><div><small>线路方案</small><strong>{transitMeta.lineSummary}</strong></div><i /><div><small>换乘</small><strong>{transitMeta.transfers} 次</strong></div></div>
            <div className="trip-transit-preview">{transitRows.slice(0, 3).map((row, index) => <div className="trip-transit-preview-row" key={`${row.title}-${index}`}><span className={`material-symbols-outlined ${row.kind}`}>{row.kind === "walk" ? "directions_walk" : "train"}</span><div><strong>{row.title}</strong><small>{row.detail}</small></div></div>)}</div>
            <div className="trip-route-actions transit"><button onClick={onOpenDetail}><span className="material-symbols-outlined">format_list_bulleted</span>今日安排</button><button className="primary" onClick={transitOpen ? onCloseTransit : onOpenTransit}><span className="material-symbols-outlined">tram</span>{transitOpen ? "收起站点路线" : "查看站点路线"}</button></div>
            {transitOpen && <div className="trip-transit-sheet"><div className="trip-transit-sheet-head"><div><small>BEST TRANSIT PLAN</small><strong>{point?.name} → {nextPoint?.name}</strong></div><button onClick={onCloseTransit} aria-label="收起站点路线"><span className="material-symbols-outlined">expand_more</span></button></div><ol>{transitRows.map((row, index) => <li key={`${row.title}-${index}`}><span className={`material-symbols-outlined ${row.kind}`}>{row.kind === "walk" ? "directions_walk" : "subway"}</span><div><strong>{row.title}</strong><small>{row.detail}</small></div></li>)}</ol><div className="trip-transit-sheet-actions"><button type="button" onClick={() => continueInAmap(point, nextPoint, transportation)}><span className="material-symbols-outlined">open_in_new</span>去高德继续</button></div></div>}
          </>
        : <>
            <div className="trip-route-stats"><div><strong>{duration.replace("分钟", "")}</strong><small>分钟</small><span>{distance}</span></div><i /><div><strong>14:30</strong><span>预计到达</span></div><i /><div><span className="material-symbols-outlined">wb_sunny</span><strong>26°C</strong></div></div>
            <div className="trip-route-actions"><button onClick={onOpenDetail}><span className="material-symbols-outlined">format_list_bulleted</span>今日安排</button><button className="primary" onClick={onNavigate}><span className="material-symbols-outlined">navigation</span>开始导航</button></div>
          </>}
    </div>
    {adjustExpanded ? <div className="trip-adjust-overlay">
      <section className="trip-adjust-panel" aria-label="小渡调整行程对话">
        <div className="trip-adjust-panel-grip" />
        <header className="trip-adjust-panel-head">
          <div><span className="material-symbols-outlined">auto_awesome</span><div><strong>小渡 · 调整行程</strong><small>{data.city} · 第 {day.day} 天</small></div></div>
          <button onClick={onAdjustCollapse} aria-label="收起对话"><span className="material-symbols-outlined">keyboard_arrow_down</span></button>
        </header>
        <div className="trip-adjust-panel-messages">
          {adjustMessages.map((message, index) => <div key={index} className={`trip-chat-bubble ${message.role}`}>{message.text}</div>)}
          {adjustBusy && <div className="trip-chat-bubble ai typing"><i /><i /><i /></div>}
        </div>
        <form className={`trip-adjust-panel-input ${adjustText ? "has-text" : ""}`} onSubmit={(event) => { event.preventDefault(); onAdjustSubmit(adjustText); }}>
          <input value={adjustText} onChange={(event) => onAdjustText(event.target.value)} placeholder="继续告诉小渡你的想法..." aria-label="继续调整行程" autoFocus />
          <button type="button" className={`trip-adjust-voice btn-stop ${adjustBusy ? "is-stopping" : ""}`} onClick={onAdjustStop} disabled={!adjustBusy} aria-label="停止生成" title="停止生成"><span className="stop-square" /></button>
          <button type="submit" className={`trip-adjust-send ${adjustBusy ? "while-generating" : ""}`} disabled={!adjustText.trim() || adjustBusy} aria-label="发送"><span className="material-symbols-outlined">arrow_upward</span></button>
        </form>
      </section>
    </div> : <form className={`trip-ai-adjust ${adjustText ? "has-text" : ""}`} onSubmit={(event) => { event.preventDefault(); onAdjustSubmit(adjustText); }}>
      <span className="trip-adjust-brand material-symbols-outlined">auto_awesome</span>
      <input value={adjustText} onChange={(event) => onAdjustText(event.target.value)} placeholder="询问摆渡人以调整行程..." aria-label="询问摆渡人以调整行程" />
      <button type="button" className={`trip-adjust-voice btn-stop ${adjustBusy ? "is-stopping" : ""}`} onClick={onAdjustStop} disabled={!adjustBusy} aria-label="停止生成" title="停止生成"><span className="stop-square" /></button>
      <button type="submit" className={`trip-adjust-send ${adjustBusy ? "while-generating" : ""}`} disabled={!adjustText.trim() || adjustBusy} aria-label="发送"><span className="material-symbols-outlined">arrow_upward</span></button>
    </form>}
    <TripDock onChange={onNavChange} />
  </section>;
}

function ProfileView({ onNavChange, identity, isDeveloper, personalized, onPersonalizedChange }: { onNavChange: (tab: Tab) => void; identity: string | null; isDeveloper: boolean; personalized: boolean; onPersonalizedChange: (value: boolean) => void }) {
  const [navBroadcast, setNavBroadcast] = useState(true);
  const [aggressiveRoute, setAggressiveRoute] = useState(false);

  const trips = [
    { title: "新东京市夜航赛博游", meta: "2023.10.15 · 3天2晚", img: "https://lh3.googleusercontent.com/aida-public/AB6AXuDU7-m9QkceBVS_DOwvZ8hMMtKQmcoGqm9BNCNXBDqDl2iK_rcGOxsaPH1rk-ldEbWk5vF6Bdsapt9-M5-HxzgdZZV7_X3VhW5oTQSFjL4OjSjESO7WUqsrP890dEDt5fTpKZfeb2mvDggH1ZUMa4D_8ZcFN4R5mSuJiYGEof0DDl8PiWjLqnPBtjWm9dhAMqKB04Y1pDmQ8aPiyYflnr-K02-k7MSw0TVb8WzVxc9x7cVkFxqBwTLA" },
    { title: "极光追踪行动", meta: "2023.01.05 · 7天6晚", img: "https://lh3.googleusercontent.com/aida-public/AB6AXuBrAql9TH3pcsopoD7tO-UUat0MX5McifywGETzPFJGvsyuXaCUb5Te9xKH5KW3Ww1GYvFnm-cFvVNk6TQR3lEFd8FdxtoOhTpiAR3DWQLP3kdiJWRjoquqDR7SMTbv-FP-T6WEb4tcTFXhp70F43zB1apNNPGr-SNJ-e7p0X-ftUVoGDbz3lv8Hqw4d6d0B_9SkbW4A2dp7ogKlWvhutonzOOaBqFfveyMWESFs3xs2rSN1BTL1I6R" },
  ];

  const places = [
    { title: "霓虹深渊酒吧", coord: "坐标 A-09", cls: "purple", img: "https://lh3.googleusercontent.com/aida-public/AB6AXuAX9GTXVIScHz-aKdeVjxgEjEhMc4HOT8-_Fy61g7VzqGsPAD1l_RBJGJ4_haiFhH5RttKLymhgyJSYWLSMP-Mj-m9aQbXN5M2i3ieODt5pZ_pqbl1qYWolyGoQGGYg9zXHtUe7-ZxZa1RdByoWxXiQZIevo0jlcBPX4w4z0W43XjwaM3rdlifeibh3Yl7XOAe8leHsRLgMw-Gv5bPM8ESUHZqmBlsZUs9a4ZY1gjLkLLtCE-4X8HPy" },
    { title: "星穹观测台", coord: "坐标 B-12", cls: "primary", img: "https://lh3.googleusercontent.com/aida-public/AB6AXuBORtLoU4B4Fvi-2o4KN0agdFV12Ry7R6OiHe5hGx1r_3QCvSbdMgTJFKPdCaTyOl5GLFFCenfIuBuVnCZdkkB_UQEbWINK37lbj9m-QyyTw1KFG1P_TIPPJPFzRVMvEs5sQT3Ps6hswSBCd3pZlOgqpKYnz_ZHBoOZFwF_PB5IYPJEXfkzMmQUt7fbn_5rQ9rYGlFbpAoJ4ASv9X5m1oC-gpUWA9OPv_khy1vJ9BIfuTII7VxeY1Q4" },
  ];

  return (
    <section className="profile-screen">
      {/* 固定 Header */}
      <header className="profile-header">
        <h1>我的</h1>
        <button className="profile-header-avatar" aria-label="用户中心">
          <span className="material-symbols-outlined">person</span>
        </button>
      </header>

      {/* 可滚动主区域 */}
      <main className="profile-main">
        {/* 个人资料头 */}
        <section className="profile-hero">
          <div className="profile-hero-bg" />
          <div className="profile-avatar-wrap">
            <div className="profile-avatar-glow" />
            <div className="profile-avatar-ring">
              <img
                src="https://lh3.googleusercontent.com/aida-public/AB6AXuD-3dmu75MW-ht4HF9tEIlesRCtT3_7vSftVo38bHD-2ZOULQgKE1Df5n0GNy4VVZZAxhSxcgDItAO4KH1sgzHKC_n7DdU6Z_vLT3UKShDgi68IDfcJtMZ5CTHeCvoKVee2Nbsq8F9zBSFvBYI1Rx0FAPBQ4Dxu10WS8zFndNMDGQOo6JGRqPURCyVyu0XxVNaYh7lZ5Q8awdCxcuBtj_XuWQXd2QBGADPCHJjoi2biitk4G03WaVDp"
                alt="Alex Chen 头像"
              />
            </div>
            <button className="profile-avatar-edit" aria-label="编辑头像">
              <span className="material-symbols-outlined">edit</span>
            </button>
          </div>
          <h2>匿名旅行者</h2>
          <p className="profile-level">
            <span className="material-symbols-outlined">verified_user</span>
            {identity ? "匿名身份已启用 · 本设备行程独立保存" : "正在建立匿名身份…"}
          </p>
          {identity && <div className="profile-identity" title={identity}>
            <span className="material-symbols-outlined">shield_person</span>
            <span>{isDeveloper ? "开发者免额度" : "旅行身份"}</span>
            <code>…{identity.slice(-8)}</code>
            <button type="button" onClick={() => void navigator.clipboard?.writeText(identity)} aria-label="复制身份标识" title="复制身份标识">
              <span className="material-symbols-outlined">content_copy</span>
            </button>
          </div>}
          <div className="profile-stats">
            <div className="profile-stat-card">
              <strong>128</strong>
              <small>探索地点</small>
            </div>
            <div className="profile-stat-card secondary">
              <strong>12.5k</strong>
              <small>巡航里程</small>
            </div>
          </div>
        </section>

        {/* 历史行程 */}
        <section className="profile-section">
          <div className="profile-section-head">
            <h3><span className="material-symbols-outlined">history</span>历史行程</h3>
            <button className="profile-section-more">查看全部</button>
          </div>
          <div className="profile-glass-list">
            {trips.map((trip, i) => (
              <Fragment key={trip.title}>
                {i > 0 && <div className="profile-divider" />}
                <button className="profile-trip-row">
                  <div className="profile-trip-thumb">
                    <div style={{ backgroundImage: `url('${trip.img}')` }} />
                  </div>
                  <div className="profile-trip-copy">
                    <h4>{trip.title}</h4>
                    <p>{trip.meta}</p>
                  </div>
                  <span className="material-symbols-outlined">chevron_right</span>
                </button>
              </Fragment>
            ))}
          </div>
        </section>

        {/* 收藏地点 */}
        <section className="profile-section">
          <div className="profile-section-head">
            <h3><span className="material-symbols-outlined filled">bookmark</span>收藏地点</h3>
          </div>
          <div className="profile-place-grid">
            {places.map((place) => (
              <div className="profile-place-card" key={place.title}>
                <div className="profile-place-img" style={{ backgroundImage: `url('${place.img}')` }} />
                <div className="profile-place-overlay" />
                <div className="profile-place-copy">
                  <h4>{place.title}</h4>
                  <p className={place.cls}>{place.coord}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* 偏好设置 */}
        <section className="profile-section">
          <div className="profile-section-head">
            <h3><span className="material-symbols-outlined">tune</span>偏好设置</h3>
          </div>
          <div className="profile-glass-list">
            <div className="profile-pref-row">
              <div className="profile-pref-icon"><span className="material-symbols-outlined">notifications_active</span></div>
              <span>智能导航播报</span>
              <label className="profile-toggle" aria-label="智能导航播报">
                <input type="checkbox" checked={navBroadcast} onChange={() => setNavBroadcast((v) => !v)} />
                <span className={`profile-toggle-track ${navBroadcast ? "on" : ""}`}>
                  <span className="profile-toggle-thumb" />
                </span>
              </label>
            </div>
            <div className="profile-divider" />
            <div className="profile-pref-row">
              <div className="profile-pref-icon"><span className="material-symbols-outlined">auto_awesome</span></div>
              <span>个性化推荐</span>
              <label className="profile-toggle" aria-label="个性化推荐">
                <input type="checkbox" checked={personalized} onChange={(event) => onPersonalizedChange(event.target.checked)} />
                <span className={`profile-toggle-track ${personalized ? "on" : ""}`}><span className="profile-toggle-thumb" /></span>
              </label>
            </div>
            <div className="profile-divider" />
            <div className="profile-pref-row">
              <div className="profile-pref-icon"><span className="material-symbols-outlined">speed</span></div>
              <span>激进路线偏好</span>
              <label className="profile-toggle" aria-label="激进路线偏好">
                <input type="checkbox" checked={aggressiveRoute} onChange={() => setAggressiveRoute((v) => !v)} />
                <span className={`profile-toggle-track ${aggressiveRoute ? "on" : ""}`}>
                  <span className="profile-toggle-thumb" />
                </span>
              </label>
            </div>
          </div>
        </section>

      </main>

      {/* 底部 Dock */}
      <nav className="ai-home-dock" aria-label="main nav">
        <div className="dock-inner">
          {([
            { id: "ai" as Tab, label: "小渡AI", img: "https://lh3.googleusercontent.com/aida-public/AB6AXuCkxaV433LVZUwvkIdUp_NsfHFINgznuM3ApGwS01S-TZshebDk34ecmROYo87QKHeFDZ1uxAAZd1iaDLUEU8vFzDlJm-BXFNFKDULXlFLpeE4bgBwrqGOOn7XMRkdUwGKaFvmIkjaNVxKk_gbrHcei4GLhaVamQmyq3sdtdlDCpKToO-Qoc3ggVomNy-7nElD1VFm9yTGQAS7OFI68a6XG-JyXsjXb7vs0OJsQZOI1-hs85fRSxLvRywNV8mebpA__YjOWUr3NWvYIqQ" },
            { id: "discover" as Tab, label: "小渡推荐", icon: "lightbulb" },
            { id: "trip" as Tab, label: "行程", icon: "explore" },
            { id: "profile" as Tab, label: "我的", icon: "account_circle" },
          ] as const).map((item) => (
            <button
              key={item.id}
              className={item.id === "profile" ? "active" : ""}
              onClick={() => onNavChange(item.id)}
            >
              {"img" in item
                ? <img src={item.img} alt={item.label} />
                : <span className="material-symbols-outlined">{item.icon}</span>}
              <small>{item.label}</small>
            </button>
          ))}
        </div>
      </nav>
    </section>
  );
}

function PanelTitle({ icon, title, action }: { icon: string; title: string; action?: string }) {
  return <div className="panel-title"><span>{icon}</span><strong>{title}</strong>{action && <button>{action}</button>}</div>;
}

function HistoryDrawer({ open, items, activeId, onClose, onNewChat, onSelect }: { open: boolean; items: ConversationHistoryItem[]; activeId: string; onClose: () => void; onNewChat: () => void; onSelect: (item: ConversationHistoryItem) => void }) {
  const [search, setSearch] = useState("");
  const visibleItems = items.filter((item) => `${item.title} ${item.preview}`.toLowerCase().includes(search.trim().toLowerCase()));
  const today = new Date().toDateString();
  const groups = [
    { title: "今天", items: visibleItems.filter((item) => new Date(item.updatedAt).toDateString() === today) },
    { title: "更早", items: visibleItems.filter((item) => new Date(item.updatedAt).toDateString() !== today), older: true },
  ].filter((group) => group.items.length);
  return <div className={`drawer-layer ${open ? "open" : ""}`} onClick={onClose}>
    <aside className={`history-drawer ${open ? "open" : ""}`} onClick={(event) => event.stopPropagation()}>
      <div className="hd-header">
        <div className="hd-title-row">
          <div className="hd-avatar"><img src={homeFan.userAvatar} alt="User" /></div>
          <h2>历史记录</h2>
        </div>
        <div className="hd-search-row">
          <label className="hd-search">
            <span className="material-symbols-outlined">search</span>
            <input placeholder="搜索聊天历史..." type="text" value={search} onChange={(event) => setSearch(event.target.value)} />
          </label>
          <button className="hd-edit" aria-label="edit" onClick={onNewChat}><span className="material-symbols-outlined">edit_square</span></button>
        </div>
      </div>
      <div className="hd-list">
        {!groups.length && <p className="hd-empty">还没有历史对话</p>}
        {groups.map((group) => (
          <div className={`hd-group ${group.older ? "older" : ""}`} key={group.title}>
            <h3>{group.title}</h3>
            <div className="hd-items">
              {group.items.map((item) => (
                <button className="hd-item" key={item.id} onClick={() => onSelect(item)}>
                  <span className={`material-symbols-outlined ${item.id === activeId ? "active" : ""}`}>chat_bubble</span>
                  <div className="hd-item-body">
                    <h4>{item.title}</h4>
                    <p>{item.preview}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="hd-user">
        <div className="hd-user-avatar"><img src={homeFan.userAvatar} alt="User Avatar" /></div>
        <div className="hd-user-info">
          <p className="hd-user-name">用户名</p>
          <p className="hd-user-email">user@example.com</p>
        </div>
        <button className="hd-settings" aria-label="settings"><span className="material-symbols-outlined">settings</span></button>
      </div>
    </aside>
  </div>;
}

export default function App() {
  // 浏览器打开即建立匿名身份，身份值只放在 HttpOnly Cookie 内。
  const [anonymousUserId, setAnonymousUserId] = useState<string | null>(null);
  const [anonymousDeveloper, setAnonymousDeveloper] = useState(false);
  const [personalizedRecommendations, setPersonalizedRecommendations] = useState(false);
  useEffect(() => {
    void ensureAnonymousIdentity().then((identity) => {
      setAnonymousUserId(identity.user_id);
      setAnonymousDeveloper(Boolean(identity.is_developer));
      setPersonalizedRecommendations(localStorage.getItem(`trip-agent-personalized:${identity.user_id}`) === "true");
    }).catch(() => undefined);
  }, []);
  const [active, setActive] = useState<Tab>("ai");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const chatAbortRef = useRef<AbortController | null>(null);
  const adjustAbortRef = useRef<AbortController | null>(null);
  const [statusText, setStatusText] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [data, setData] = useState<MapData | null>(null);
  const [activeDay, setActiveDay] = useState(0);
  const [summary, setSummary] = useState<RouteSummary>({ status: "idle", legsCompleted: 0, legsTotal: 0 });
  const [transportation, setTransportation] = useState<Transportation>("公共交通");
  const [tripSubView, setTripSubView] = useState<TripSubView>("overview");
  const [selectedPoint, setSelectedPoint] = useState(0);
  const [tripGenerating, setTripGenerating] = useState<string | null>(null);
  const [tripTransitOpen, setTripTransitOpen] = useState(false);
  const [savedCities, setSavedCities] = useState<Set<string>>(new Set());
  const [tripAdjustText, setTripAdjustText] = useState("");
  const [tripAdjustExpanded, setTripAdjustExpanded] = useState(false);
  const [tripAdjustMessages, setTripAdjustMessages] = useState<ChatMessage[]>([]);
  const [tripAdjustBusy, setTripAdjustBusy] = useState(false);
  const tripTimerRef = useRef<number | null>(null);
  const mapRef = useRef<RouteMapHandle>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState(() => newConversationId());
  const [conversationTitle, setConversationTitle] = useState("");
  const [conversationHistory, setConversationHistory] = useState<ConversationHistoryItem[]>([]);
  const [historyOwnerId, setHistoryOwnerId] = useState<string | null>(null);
  const [clarificationMissingFields, setClarificationMissingFields] = useState<string[]>([]);
  const [showFormCard, setShowFormCard] = useState(false);
  const [agentStages, setAgentStages] = useState<StageRow[]>([]);
  const [routeOptions, setRouteOptions] = useState<RouteOption[]>([]);
  const [selectedRoute, setSelectedRoute] = useState<RouteOption | null>(null);
  const [lastGuideQuery, setLastGuideQuery] = useState<string>("");
  const [lastGuideMeta, setLastGuideMeta] = useState<Record<string, unknown> | null>(null);
  const [isNewChat, setIsNewChat] = useState(false);
  const title = useMemo(() => active === "discover" ? "小渡推荐" : active === "trip" ? "行程" : active === "profile" ? "我的" : "小渡 Ai", [active]);

  const stopChat = useCallback(() => {
    if (!chatAbortRef.current) return;
    chatAbortRef.current.abort();
    chatAbortRef.current = null;
    setBusy(false);
    setAgentStages([]);
    setStatusText("已停止生成");
    setMessages((prev) => prev.map((m) => m.streaming ? { ...m, streaming: false } : m));
  }, []);

  useEffect(() => {
    if (!anonymousUserId) return;
    setHistoryOwnerId(null);
    try {
      const saved = localStorage.getItem(`trip-agent-chat-history:${anonymousUserId}`);
      setConversationHistory(saved ? JSON.parse(saved) : []);
    } catch {
      setConversationHistory([]);
    }
    setHistoryOwnerId(anonymousUserId);
  }, [anonymousUserId]);

  useEffect(() => {
    if (!anonymousUserId || historyOwnerId !== anonymousUserId) return;
    try {
      localStorage.setItem(`trip-agent-chat-history:${anonymousUserId}`, JSON.stringify(conversationHistory.slice(0, 30)));
    } catch {
      // 浏览器存储不可用时，当前会话仍可正常使用。
    }
  }, [anonymousUserId, conversationHistory, historyOwnerId]);

  useEffect(() => {
    if (!messages.length || !conversationTitle) return;
    const latestText = [...messages].reverse().find((message) => message.text.trim())?.text || conversationTitle;
    const item: ConversationHistoryItem = {
      id: conversationId,
      title: conversationTitle,
      preview: cleanGuideMarkdown(latestText).replace(/\s+/g, " ").trim().slice(0, 80),
      updatedAt: new Date().toISOString(),
      messages,
      sessionId,
      mapData: data,
    };
    setConversationHistory((previous) => [item, ...previous.filter((entry) => entry.id !== conversationId)].slice(0, 30));
  }, [conversationId, conversationTitle, data, messages, sessionId]);

  const stopAdjust = useCallback(() => {
    if (!adjustAbortRef.current) return;
    adjustAbortRef.current.abort();
    adjustAbortRef.current = null;
    setTripAdjustBusy(false);
    setTripAdjustMessages((prev) => [...prev, { role: "ai", text: "已停止回复。" }]);
  }, []);

  const newChat = useCallback(() => {
    stopChat();
    setMessages([]);
    setConversationId(newConversationId());
    setConversationTitle("");
    setRouteOptions([]);
    setSelectedRoute(null);
    setLastGuideQuery("");
    setLastGuideMeta(null);
    setInput("");
    setStatusText("");
    setSessionId(null);
    setClarificationMissingFields([]);
    setShowFormCard(false);
    setAgentStages([]);
    setActive("ai");
    setDrawerOpen(false);
    setIsNewChat(true);
  }, [stopChat]);

  const restoreConversation = useCallback((item: ConversationHistoryItem) => {
    stopChat();
    setConversationId(item.id);
    setConversationTitle(item.title);
    setMessages(item.messages || []);
    setSessionId(item.sessionId || null);
    setData(item.mapData || null);
    setRouteOptions([]);
    setSelectedRoute(null);
    setStatusText("");
    setShowFormCard(false);
    setAgentStages([]);
    setActive("ai");
    setIsNewChat(false);
    setDrawerOpen(false);
  }, [stopChat]);

  const loadMap = useCallback(async (nextSessionId: string, signal?: AbortSignal): Promise<MapData | null> => {
    const mapData = await fetchMapData(nextSessionId, signal);
    if (signal?.aborted) return null;
    if (mapData.days.length) {
      setData(mapData);
      setActiveDay(0);
      setSelectedPoint(0);
      setTripSubView("overview");
      setTripTransitOpen(false);
      setSummary({ status: "idle", legsCompleted: 0, legsTotal: 0 });
      // 不再自动跳行程页：生成后先在对话里确认，用户点「查看完整行程」再切
      return mapData;
    }
    return null;
  }, []);

  useEffect(() => () => {
    chatAbortRef.current?.abort();
    adjustAbortRef.current?.abort();
    if (tripTimerRef.current !== null) window.clearTimeout(tripTimerRef.current);
  }, []);

  const openSeedTrip = useCallback((seed: TripSeed) => {
    if (tripTimerRef.current !== null) window.clearTimeout(tripTimerRef.current);
    setTripGenerating(seed.city);
    setData(buildCityTrip(seed.city));
    setActiveDay(0);
    setSelectedPoint(0);
    setTransportation("公共交通");
    setTripSubView("overview");
    setTripTransitOpen(false);
    setSummary({ status: "idle", legsCompleted: 0, legsTotal: 0 });
    setActive("trip");
    tripTimerRef.current = window.setTimeout(() => setTripGenerating(null), 1100);
  }, []);

  const selectTripPoint = useCallback((point: MapPoint) => {
    const points = data?.days[activeDay]?.points || [];
    const index = points.findIndex((item) => item.poi_id === point.poi_id);
    if (index >= 0) setSelectedPoint(index);
  }, [activeDay, data]);

  const submitTripAdjust = useCallback(async (text: string) => {
    const clean = text.trim();
    if (!clean || tripAdjustBusy || adjustAbortRef.current) return;

    // 若无真实 session（仍是本地演示数据），给出明确提示，不伪装成功
    if (!sessionId || !data) {
      setTripAdjustExpanded(true);
      setTripAdjustMessages((prev) => [
        ...prev,
        { role: "user", text: clean },
        { role: "ai", text: "请先在首页生成一个真实行程，再来调整哦～" },
      ]);
      setTripAdjustText("");
      return;
    }

    setTripAdjustExpanded(true);
    setTripAdjustMessages((prev) => [...prev, { role: "user", text: clean }]);
    setTripAdjustText("");
    const controller = new AbortController();
    adjustAbortRef.current = controller;
    setTripAdjustBusy(true);

    try {
      await ensureAnonymousIdentity();
      const result = await reviseTripFromText(
        sessionId,
        clean,
        // 传当前展示的天（1-indexed）
        data.days[activeDay]?.day ?? activeDay + 1,
        controller.signal,
      );

      if (controller.signal.aborted) return;
      setTripAdjustMessages((prev) => [
        ...prev,
        { role: "ai", text: result.message || "行程已调整好啦，还想再改什么吗？" },
      ]);

      // 修订成功后刷新地图，保持当前天（越界则回退到第 0 天）
      if (result.status === "ok") {
        try {
          const mapData = await fetchMapData(sessionId, controller.signal);
          if (controller.signal.aborted) return;
          if (mapData.days.length) {
            setData(mapData);
            // 尽量保持当前天，若该天已不存在则回退
            const safeDayIdx = Math.min(activeDay, mapData.days.length - 1);
            setActiveDay(safeDayIdx);
            setSelectedPoint(0);
            setSummary({ status: "idle", legsCompleted: 0, legsTotal: 0 });
          }
        } catch {
          if (controller.signal.aborted) return;
          setTripAdjustMessages((prev) => [...prev, {
            role: "ai", text: "行程已保存，但页面路线刷新失败，当前仍显示旧路线，请重试刷新。",
          }]);
        }
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setTripAdjustMessages((prev) => [
        ...prev,
        {
          role: "ai",
          text: error instanceof Error
            ? `调整遇到问题：${error.message}`
            : "调整请求失败，请稍后重试。",
        },
      ]);
    } finally {
      if (adjustAbortRef.current === controller) {
        adjustAbortRef.current = null;
        setTripAdjustBusy(false);
      }
    }
  }, [sessionId, activeDay, tripAdjustBusy, data]);

  const sendQuery = useCallback(async (query: string, showUserMessage = true, inspiration?: TrendingInspiration) => {
    const clean = query.trim();
    if (!clean || chatAbortRef.current) return;
    const controller = new AbortController();
    const conversationContext = buildConversationContext(messages);
    chatAbortRef.current = controller;
    if (showUserMessage) setMessages((prev) => [...prev, { role: "user", text: clean }]);
    if (showUserMessage && !conversationTitle) setConversationTitle(conversationTitleFromQuery(clean));
    // 澄清卡只属于触发它的那一轮规划。用户开始新一句话时先关闭，避免在闲聊/取消时残留。
    if (showUserMessage) {
      setShowFormCard(false);
      setClarificationMissingFields([]);
    }
    setInput("");
    setBusy(true);
    setAgentStages([]);  // 重置大师工作流
    setRouteOptions([]);
    setSelectedRoute(null);
    setActive("ai");
    setIsNewChat(false);
    try {
      await ensureAnonymousIdentity();
      if (controller.signal.aborted) return;
      // 所有输入交给同一个后端路由，结合 Session 决定对话、回顾、修改或新规划。
      setLastGuideQuery(clean);
      // 不预先加空 streaming 消息；等收到第一个 guide_delta 再创建，避免失败时留下空消息+闪烁光标
      const guideResult = await generateGuideRoutesStream(clean, sessionId, conversationContext, {
        onStage: (event) => {
          if (controller.signal.aborted) return;
          setAgentStages((prev) => {
            const idx = prev.findIndex((s) => s.agent === event.agent);
            const row: StageRow = { agent: event.agent, label: event.label, status: event.status, weather: event.weather };
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = row;
              return next;
            }
            return [...prev, row];
          });
        },
        onGuideDelta: (content) => {
          if (controller.signal.aborted) return;
          setMessages((prev) => {
            // 找到最后一条 streaming AI 消息；没有就创建一条
            const lastIdx = [...prev].reverse().findIndex((m) => m.role === "ai" && m.streaming);
            const realIdx = lastIdx >= 0 ? prev.length - 1 - lastIdx : -1;
            if (realIdx >= 0) {
              const next = [...prev];
              next[realIdx] = { ...next[realIdx], text: next[realIdx].text + content };
              return next;
            }
            return [...prev, { role: "ai", text: content, streaming: true }];
          });
        },
      }, controller.signal, showUserMessage ? "full" : "inspiration", inspiration);
      if (controller.signal.aborted) return;
      setAgentStages([]);
      if (guideResult.status === "ok" && (guideResult.action || "route_options") === "route_options") {
        setRouteOptions(guideResult.route_options || []);
        setLastGuideMeta(guideResult.trip_meta || null);
        // 成功：关掉 streaming 标记，保留攻略文案
        setMessages((prev) => prev.map((m) => m.streaming ? { ...m, streaming: false } : m));
        setStatusText("请选择一条路线方向，我再继续落地成行程");
      } else if (guideResult.status === "ok") {
        const message = guideResult.assistant_message || guideResult.error_message || "好的。";
        setLastGuideMeta(null);
        setMessages((prev) => [...prev.filter((m) => !m.streaming), { role: "ai", text: message }]);
        setStatusText("");
        if (guideResult.action === "current_trip_modify" && guideResult.itinerary_updated && sessionId) {
          try {
            await loadMap(sessionId, controller.signal);
          } catch {
            // 修改已写入 Session；地图读取失败不影响对话结果。
          }
        }
      } else {
        // 失败：删掉半截 streaming 消息（如果有），避免和澄清/错误消息叠加成两个 AI 头像
        setMessages((prev) => {
          const withoutStreaming = prev.filter((m) => !m.streaming);
          if (guideResult.error_code === "NEEDS_CLARIFICATION") {
            setClarificationMissingFields(["days", "preferences", "budget"]);
            setShowFormCard(true);
            return [...withoutStreaming, { role: "ai", text: guideResult.error_message || "我还需要一点出行信息，比如天数、偏好或预算。" }];
          }
          if (guideResult.action === "current_trip_modify") {
            return [...withoutStreaming, { role: "ai", text: guideResult.error_message || "这次行程调整没有完成，请换个说法再试一次。" }];
          }
          if (guideResult.error_code === "QUOTA_EXCEEDED") {
            return [...withoutStreaming, { role: "ai", text: guideResult.error_message || "今天的试用次数已用完，明天再来继续安排吧。" }];
          }
          if (["UPSTREAM_TIMEOUT", "UPSTREAM_UNAVAILABLE", "INTERNAL_ERROR"].includes(guideResult.error_code || "")) {
            return [...withoutStreaming, { role: "ai", text: guideResult.error_message || "旅行数据服务暂时不可用，请稍后重试。", guideFailed: true }];
          }
          return [...withoutStreaming, { role: "ai", text: "攻略暂时生成不出来，要不要换个说法再试一次？", guideFailed: true }];
        });
        setStatusText("");
      }
      return;
    } catch (error) {
      if (controller.signal.aborted) return;
      setAgentStages([]);
      setMessages((prev) => [...prev, { role: "ai", text: "网络有点不稳定，要不要再试一次？", guideFailed: true }]);
      setStatusText("");
    } finally {
      if (chatAbortRef.current === controller) {
        chatAbortRef.current = null;
        setBusy(false);
      }
    }
  }, [conversationTitle, loadMap, messages, sessionId]);

  const openTrendingPlanPreview = useCallback((note: TrendingNote) => {
    if (chatAbortRef.current) return;
    setActive("ai");
    setIsNewChat(false);
    setRouteOptions([]);
    setSelectedRoute(null);
    setStatusText("");
    setMessages((prev) => [...prev, {
      role: "ai",
      text: "这篇笔记很适合作为旅行灵感。先看看我为你设计的路线方向，再决定要不要生成完整行程。",
      trendingNote: note,
    }]);
  }, []);

  const generateTrendingPlans = useCallback((note: TrendingNote) => {
    if (busy || chatAbortRef.current) return;
    // 这是内部的规划上下文，只传给服务端，不呈现为用户在对话框里说的话。
    const inspiration = buildTrendingInspiration(note);
    const planningContext = `根据已选旅行灵感「${note.title}」整理 3 个路线方向。`;
    setMessages((prev) => [
      ...prev.map((message) => message.trendingNote?.id === note.id
        ? { ...message, trendingPlansRequested: true }
        : message),
      { role: "ai", text: "正在根据这篇旅行灵感整理 3 个路线方案…", streaming: true },
    ]);
    void sendQuery(planningContext, false, inspiration);
  }, [busy, sendQuery]);

  const handleRouteSelect = useCallback(async (route: RouteOption) => {
    if (busy || chatAbortRef.current) return;
    const controller = new AbortController();
    chatAbortRef.current = controller;
    setSelectedRoute(route);
    setBusy(true);
    setRouteOptions([]);
    setAgentStages([]);
    setStatusText("已选择路线，正在搜索真实景点和酒店");
    setMessages((prev) => [...prev, {
      role: "ai",
      text: `已选择：${route.title}\n${route.route_text ? `路线：${route.route_text}\n` : ""}我现在基于这条路线搜索真实景点、排程并推荐酒店。`,
    }]);
    try {
      const result = await planFromRouteStream(lastGuideQuery || route.route_text || route.title, route, sessionId, lastGuideMeta, (event) => {
        if (controller.signal.aborted) return;
        setAgentStages((prev) => {
          const idx = prev.findIndex((s) => s.agent === event.agent);
          const row: StageRow = { agent: event.agent, label: event.label, status: event.status, weather: event.weather };
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = row;
            return next;
          }
          return [...prev, row];
        });
      }, controller.signal);
      if (controller.signal.aborted) return;
      setAgentStages([]);
      if (result.session_id) setSessionId(result.session_id);
      if (result.status === "ok" && result.session_id) {
        setStatusText("行程已就绪，正在加载路线");
        const mapData = await loadMap(result.session_id, controller.signal);
        if (controller.signal.aborted) return;
        setStatusText("");
        const plan = result.trip_plan;
        const city = plan?.city || result.trip_meta?.city || "目的地";
        const daysCount = result.trip_meta?.days || mapData?.days.length || plan?.days?.length || 0;
        const dayLines: string[] = [];
        const mapDays = mapData?.days || [];
        for (let i = 0; i < Math.min(3, mapDays.length || plan?.days?.length || 0); i++) {
          const mapDay = mapDays[i];
          const planDay = plan?.days?.[i];
          const routeNames = (mapDay?.points || []).map((p) => p.name).join(" → ");
          const routeText = routeNames || planDay?.description || "轻松漫步的一天";
          dayLines.push(`📍 Day${(mapDay?.day) || (i + 1)}：${routeText}`);
        }
        const hotelLines: string[] = [];
        const seenHotels = new Set<string>();
        // 优先展示后端返回的 5 个候选酒店（名称+评分+星级），不显示价格和链接
        for (const h of result.hotel_candidates || []) {
          if (!h.name || seenHotels.has(h.name)) continue;
          seenHotels.add(h.name);
          const bits = [h.rating ? `评分 ${h.rating}` : "", h.type || ""].filter(Boolean).join(" ｜ ");
          hotelLines.push(`🏨 ${h.name}${bits ? `\n   ${bits}` : ""}`);
        }
        // 兜底：如果候选为空，用 plan.days[].hotel
        if (hotelLines.length === 0) {
          for (const d of plan?.days || []) {
            const h = d.hotel;
            if (!h?.name || seenHotels.has(h.name)) continue;
            seenHotels.add(h.name);
            const bits = [h.rating ? `评分 ${h.rating}` : "", h.type || ""].filter(Boolean).join(" ｜ ");
            hotelLines.push(`🏨 ${h.name}${bits ? `\n   ${bits}` : ""}`);
          }
        }
        const weatherTip = buildWeatherTip((plan?.weather_info || []).map((w) => ({
          city: "", date: w.date || "", day_weather: w.day_weather || "", night_weather: w.night_weather || "", day_temp: String(w.day_temp ?? ""), night_temp: String(w.night_temp ?? ""),
        })));
        const summaryText = [
          `${city} ${daysCount} 日行程已备好 ✦`,
          dayLines.length ? "\n" + dayLines.join("\n") : "",
          hotelLines.length ? "\n" + hotelLines.join("\n") : "",
          weatherTip ? `\n🌤 ${weatherTip}` : "",
        ].filter(Boolean).join("\n");
        setMessages((prev) => [...prev, { role: "ai", text: summaryText, tripReady: true }]);
      } else {
        setStatusText("");
        setMessages((prev) => [...prev, { role: "ai", text: result.error_message || "这条路线暂时没能落地成行程，可以换一条路线试试。" }]);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setAgentStages([]);
      setStatusText("");
      setMessages((prev) => [...prev, { role: "ai", text: error instanceof Error ? error.message : "路线规划失败，请稍后重试。" }]);
    } finally {
      if (chatAbortRef.current === controller) {
        chatAbortRef.current = null;
        setBusy(false);
      }
    }
  }, [busy, lastGuideMeta, lastGuideQuery, loadMap, sessionId]);

  const handleRegenerateGuide = useCallback(() => {
    if (!lastGuideQuery) return;
    // 清掉最后一条失败提示消息，避免重新生成后还留着旧错误
    setMessages((prev) => {
      if (prev.length && prev[prev.length - 1].guideFailed) {
        return prev.slice(0, -1);
      }
      return prev;
    });
    void sendQuery(lastGuideQuery);
  }, [lastGuideQuery, sendQuery]);

  const handleEditMessage = useCallback((index: number, text: string) => {
    // 编辑：把 AI 消息内容回填输入框，并删掉这条 AI 消息及之后的全部消息（相当于回退到这轮对话前）
    setInput(text);
    setMessages((prev) => prev.slice(0, index));
  }, []);

  const handleRegenerateMessage = useCallback((index: number) => {
    if (busy) return;
    // 重新生成：找到这条 AI 消息对应的上一条 user 消息，删掉这条 AI 及之后的消息，重跑
    setMessages((prev) => {
      let userIdx = -1;
      for (let i = index - 1; i >= 0; i--) {
        if (prev[i].role === "user") { userIdx = i; break; }
      }
      if (userIdx < 0) return prev;
      const userText = prev[userIdx].text;
      // 删掉 user 消息及之后的所有消息，重跑
      void sendQuery(userText);
      return prev.slice(0, userIdx);
    });
  }, [busy, sendQuery]);

  const handleFormSubmit = useCallback((parts: string[]) => {
    setShowFormCard(false);
    // 把原始 query（含 city）和补的字段拼在一起，避免补完后新 query 缺 city
    const base = lastGuideQuery || "";
    const supplement = parts.join("，");
    const merged = base && supplement ? `${base}，${supplement}` : (base || supplement);
    void sendQuery(merged);
  }, [lastGuideQuery, sendQuery]);

  const handleFormDismiss = useCallback(() => {
    setShowFormCard(false);
  }, []);

  const showAiHome = active === "ai" && messages.length === 0;

  return <main className="lumina-app"><div className="phone-frame">
    {showAiHome
      ? <AIDiscoverHomeView onSend={sendQuery} onProfile={() => setActive("profile")} onMenu={() => setDrawerOpen(true)} active={active} isNewChat={isNewChat} onNavChange={(tab) => { setActive(tab); if (tab !== "ai") setIsNewChat(false); }} />
      : <>
        {active === "ai" && <ChatView title={conversationTitle} messages={messages} input={input} busy={busy} statusText={statusText} showFormCard={showFormCard} missingFields={clarificationMissingFields} agentStages={agentStages} routeOptions={routeOptions} selectedRouteId={selectedRoute?.id || null} onRouteSelect={handleRouteSelect} onRegenerateGuide={handleRegenerateGuide} onGenerateTrendingPlans={generateTrendingPlans} onEditMessage={handleEditMessage} onRegenerateMessage={handleRegenerateMessage} onFormSubmit={handleFormSubmit} onFormDismiss={handleFormDismiss} onInput={setInput} onSubmit={(event) => { event.preventDefault(); void sendQuery(input); }} onStop={stopChat} onViewTrip={() => setActive("trip")} onMenu={() => setDrawerOpen(true)} onHistory={() => setDrawerOpen(true)} onNavChange={setActive} active={active} />}
        {active === "discover" && <DiscoverView onStart={openTrendingPlanPreview} onSearch={(query) => void sendQuery(query)} onProfile={() => setActive("profile")} active={active} onNavChange={setActive} onGoTrip={openSeedTrip} personalized={personalizedRecommendations} />}
        {active === "trip" && <TripView data={data} activeDay={activeDay} transportation={transportation} summary={summary} subView={tripSubView} selectedPoint={selectedPoint} transitOpen={tripTransitOpen} saved={data ? savedCities.has(data.city) : false} adjustExpanded={tripAdjustExpanded} adjustText={tripAdjustText} adjustMessages={tripAdjustMessages} adjustBusy={tripAdjustBusy} onToggleSave={() => { if (!data) return; setSavedCities((prev) => { const next = new Set(prev); next.has(data.city) ? next.delete(data.city) : next.add(data.city); return next; }); }} onAdjustStop={stopAdjust} onAdjustCollapse={() => setTripAdjustExpanded(false)} onAdjustText={setTripAdjustText} onAdjustSubmit={submitTripAdjust} onDay={(index) => { setActiveDay(index); setSelectedPoint(0); setTripSubView("overview"); setTripTransitOpen(false); setSummary({ status: "idle", legsCompleted: 0, legsTotal: 0 }); }} onMode={(mode) => { setTransportation(mode); setTripTransitOpen(false); setSummary({ status: "loading", legsCompleted: 0, legsTotal: Math.max(0, (data?.days[activeDay]?.points.length || 1) - 1) }); }} onSummary={setSummary} onSelectPoint={selectTripPoint} onOpenDetail={() => setTripSubView("detail")} onBackOverview={() => { setTripSubView("overview"); setTripTransitOpen(false); }} onOpenTransit={() => { setTripSubView("overview"); setTripTransitOpen(true); }} onCloseTransit={() => setTripTransitOpen(false)} onNavigate={() => { if (transportation === "公共交通") { setTripTransitOpen(true); setTripSubView("overview"); return; } mapRef.current?.startNavigation(); setTripSubView("navigating"); }} onExitNav={() => { mapRef.current?.clearNavigation(); setTripSubView("overview"); }} onNavChange={(tab) => { setTripSubView("overview"); setTripTransitOpen(false); setActive(tab); }} mapRef={mapRef} />}
        {active === "profile" && <ProfileView onNavChange={setActive} identity={anonymousUserId} isDeveloper={anonymousDeveloper} personalized={personalizedRecommendations} onPersonalizedChange={(value) => { setPersonalizedRecommendations(value); if (anonymousUserId) localStorage.setItem(`trip-agent-personalized:${anonymousUserId}`, String(value)); }} />}
      </>}
    {tripGenerating && <div className="trip-generating" role="status" aria-live="polite"><div className="trip-generating-orbit"><span className="material-symbols-outlined">route</span></div><small>CRAFTING YOUR JOURNEY</small><strong>正在为你串联{tripGenerating}的风景</strong><p>正在优化地点顺序与旅行节奏…</p></div>}
    <HistoryDrawer open={drawerOpen} items={conversationHistory} activeId={conversationId} onClose={() => setDrawerOpen(false)} onNewChat={newChat} onSelect={restoreConversation} />
  </div></main>;
}
