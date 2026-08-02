/**
 * P2-1 意图识别规则表
 *
 * 设计原则：
 * - 规则按声明顺序匹配，第一条命中即返回，不再继续。
 * - 关键词足够宽松以覆盖口语化输入，但也要足够精确避免误判。
 * - 误判代价只是不够优化的 prompt 偏置，不旁路到死胡同（项目 plan §2.2）。
 *
 * 命中示例与反例见 tests/intent.test.ts。
 */
import type { IntentRule } from "./types.js";

export const INTENT_RULES: readonly IntentRule[] = [
  {
    id: "new_plan",
    // 规划/安排天/几日游/帮我计划/制定行程
    // 注意：单独 "行程" 太宽泛（会抢 "检查行程"），只在动词组合时命中
    pattern: /(规划|安排.*天|几日游|几日行程|帮我.*计划|帮我.*规划|制定.*行程)/,
    intent: "new_plan",
    modeBias: "planner",
    description: "新建行程或规划请求",
  },
  {
    id: "revise_day",
    // 修改/改天/换成/换景点/去掉poi/调整日程/删景点/加景点到
    // "换成" 覆盖 "把 X 换成 Y" 的修改语义
    pattern: /(修改|改.*天|换成|换.*景点|换.*poi|去掉.*poi|调整.*日程|删.*景点|加.*景点到)/i,
    intent: "revise_day",
    modeBias: "reflection",
    description: "修改已有行程的某天",
  },
  {
    id: "verify",
    // 检查/验证/verify/超预算/有没有问题
    pattern: /(检查|验证|verify|有没有问题|超.*预算|超支|合理吗|可行吗)/i,
    intent: "verify",
    modeBias: "reflection",
    description: "检查行程合理性",
  },
  {
    id: "search_poi",
    // 景点/poi/玩什么/去哪/有什么看
    pattern: /(景点|poi|玩什么|去哪玩|去哪儿|有什么.*看|有什么.*玩|好玩的地方|推荐.*景点)/i,
    intent: "search_poi",
    modeBias: "react",
    description: "搜索景点 POI",
  },
  {
    id: "search_hotel",
    // 酒店/住宿/住哪/宾馆/民宿/旅馆/hotel/hostel
    pattern: /(酒店|住宿|住哪|住哪儿|宾馆|民宿|旅馆|hotel|hostel|accommodation)/i,
    intent: "search_hotel",
    modeBias: "react",
    description: "搜索酒店住宿",
  },
  {
    id: "search_flight",
    // 机票/航班/飞机
    pattern: /(机票|航班|飞机|飞.*大阪|飞.*东京|国际航班|flight)/i,
    intent: "search_flight",
    modeBias: "react",
    description: "搜索机票航班",
  },
  {
    id: "search_train",
    // 火车/高铁/新干线/12306
    pattern: /(火车|高铁|新干线|12306|jr.*pass| jr pass|train ticket)/i,
    intent: "search_train",
    modeBias: "react",
    description: "搜索火车票/新干线",
  },
];
