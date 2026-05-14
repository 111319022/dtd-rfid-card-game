/**
 * 單讀卡區雙人對戰狀態機（剪刀石頭布）
 * 平手：雙方本回合不受傷害（0 傷）。
 * 多回合：一方 HP ≤ 0 時結束；每回合 P1 出牌（可技能+出拳，或只出拳）→ P2 出牌 → 揭曉 → 結算。
 */

export const Phase = {
  LOBBY: 'lobby',
  WAIT_P1_CHAR: 'wait_p1_char',
  WAIT_P2_CHAR: 'wait_p2_char',
  TURN_P1: 'turn_p1',
  TURN_P2: 'turn_p2',
  RPS_PENDING_REVEAL: 'rps_pending_reveal',
  RPS_REVEALED: 'rps_revealed',
  SHOW_RESULT: 'show_result',
  MATCH_OVER: 'match_over',
};

const MS_REVEAL_HIDDEN = 2000;
const MS_REVEAL_TO_DAMAGE = 1250;
const MS_POST_RESULT = 3200;
const MS_RESULT_TO_TURN_P1 = 700;

const RPS_ORDER = ['scissors', 'rock', 'paper'];

/** @returns {-1|0|1} 若 a 贏則 1，平手 0，b 贏 -1 */
export function compareRps(a, b) {
  if (a === b) return 0;
  const ia = RPS_ORDER.indexOf(a);
  const ib = RPS_ORDER.indexOf(b);
  if (ia < 0 || ib < 0) return 0;
  if ((ia + 1) % 3 === ib) return -1;
  if ((ib + 1) % 3 === ia) return 1;
  return 0;
}

function atkForCharacter(c, move) {
  if (!c || c.type !== 'CHARACTER') return 0;
  const k = `atk_${move}`;
  const v = c[k];
  return typeof v === 'number' && !Number.isNaN(v) ? v : 0;
}

function mulForSkill(skill, move) {
  if (!skill) return 1;
  const k = `mul_${move}`;
  const v = skill[k];
  if (typeof v !== 'number' || Number.isNaN(v) || v < 0) return 1;
  return v;
}

function clampHp(hp, max) {
  return Math.max(0, Math.min(max, hp));
}

function cloneSkillBuff(card) {
  return {
    mul_scissors: card.mul_scissors ?? 1,
    mul_rock: card.mul_rock ?? 1,
    mul_paper: card.mul_paper ?? 1,
    hp_heal: card.hp_heal ?? 0,
    name: card.name || '',
  };
}

function rpsRevealMode(phase) {
  if (phase === Phase.RPS_PENDING_REVEAL) return 'hidden';
  if (phase === Phase.RPS_REVEALED) return 'moves';
  if (phase === Phase.SHOW_RESULT || phase === Phase.MATCH_OVER) return 'full';
  return 'none';
}

/**
 * @param {{ onUpdate: (snap: object) => void, onToast?: (msg: string) => void }} opts
 */
export function createBattleController({ onUpdate, onToast }) {
  const toast = msg => {
    if (onToast) onToast(msg);
  };

  let phase = Phase.LOBBY;
  let round = 0;
  /** @type {object|null} */
  let p1Char = null;
  /** @type {object|null} */
  let p2Char = null;
  let p1Hp = 0;
  let p2Hp = 0;
  let p1Max = 0;
  let p2Max = 0;
  /** @type {object|null} 本回合 P1 技能加成（倍率＋已套用回血） */
  let p1SkillBuff = null;
  let p2SkillBuff = null;
  let p1Move = null;
  let p2Move = null;
  /** @type {object|null} */
  let lastResult = null;
  let resultTimer = null;

  function clearResultTimer() {
    if (resultTimer) {
      clearTimeout(resultTimer);
      resultTimer = null;
    }
  }

  function isSkillDrawRound() {
    if (round < 10) return false;
    if ((round - 10) % 3 !== 0) return false;
    return phase === Phase.TURN_P1 || phase === Phase.TURN_P2;
  }

  function snapshot() {
    const reveal = rpsRevealMode(phase);
    const showMovesToUi = reveal === 'moves' || reveal === 'full';
    // 技能在猜拳揭曉前不能讓對方知道內容(僅顯示「已使用技能」)
    const skillsRevealed = phase === Phase.SHOW_RESULT || phase === Phase.MATCH_OVER;

    return {
      phase,
      round,
      skillDrawRound: isSkillDrawRound(),
      p1: p1Char
        ? {
            name: p1Char.name || 'P1',
            hp: p1Hp,
            maxHp: p1Max,
            uid: p1Char.uid,
          }
        : null,
      p2: p2Char
        ? {
            name: p2Char.name || 'P2',
            hp: p2Hp,
            maxHp: p2Max,
            uid: p2Char.uid,
          }
        : null,
      p1Skill: skillsRevealed ? p1SkillBuff : null,
      p2Skill: skillsRevealed ? p2SkillBuff : null,
      p1SkillUsed: !!p1SkillBuff,
      p2SkillUsed: !!p2SkillBuff,
      lastResult,
      rpsReveal: reveal,
      p1Move: showMovesToUi ? p1Move : null,
      p2Move: showMovesToUi ? p2Move : null,
      prompt: promptText(),
      showStartMatch: phase === Phase.LOBBY || phase === Phase.MATCH_OVER,
      matchWinner: phase === Phase.MATCH_OVER ? lastResult?.matchWinner ?? null : null,
    };
  }

  function emit() {
    onUpdate(snapshot());
  }

  function promptText() {
    switch (phase) {
      case Phase.LOBBY:
        return '請再感應一次「啟動卡」開始本場。';
      case Phase.WAIT_P1_CHAR:
        return '請 P1 感應角色卡（CHARACTER）';
      case Phase.WAIT_P2_CHAR:
        return '請 P2 感應角色卡（CHARACTER）';
      case Phase.TURN_P1:
        return p1SkillBuff
          ? '請 P1 感應「出拳卡」完成本回合'
          : '請 P1 出牌：可先感應一張技能卡（選用），再感應出拳卡';
      case Phase.TURN_P2:
        return p2SkillBuff
          ? '請 P2 感應「出拳卡」完成本回合'
          : '請 P2 出牌：可先感應一張技能卡（選用），再感應出拳卡';
      case Phase.RPS_PENDING_REVEAL:
        return '雙方已出拳，揭曉中…';
      case Phase.RPS_REVEALED:
        return '結算中…';
      case Phase.SHOW_RESULT:
        return '本回合結果';
      case Phase.MATCH_OVER:
        return '對戰結束　—　再感應「啟動卡」開始新局';
      default:
        return '';
    }
  }

  function resetCharacters() {
    p1Char = p2Char = null;
    p1Hp = p2Hp = p1Max = p2Max = 0;
    p1SkillBuff = p2SkillBuff = null;
    p1Move = p2Move = null;
    lastResult = null;
    round = 0;
  }

  /** 進入機台（啟動卡）：清空上一場，待在候機室 */
  function enterArcade() {
    clearResultTimer();
    resetCharacters();
    phase = Phase.LOBBY;
    emit();
  }

  /** 候機室 → 等待 P1 角色 */
  function startMatch() {
    clearResultTimer();
    if (phase !== Phase.LOBBY && phase !== Phase.MATCH_OVER) {
      toast('目前無法開始：請先完成或重置對戰。');
      return;
    }
    resetCharacters();
    phase = Phase.WAIT_P1_CHAR;
    emit();
  }

  function beginRoundAfterResult() {
    p1SkillBuff = p2SkillBuff = null;
    p1Move = p2Move = null;
    lastResult = null;
    if (p1Hp <= 0 || p2Hp <= 0) {
      phase = Phase.MATCH_OVER;
      emit();
      return;
    }
    round += 1;
    phase = Phase.TURN_P1;
    emit();
  }

  /** 扣血、寫入 lastResult、進入 SHOW_RESULT，並排程下一回合或終局 */
  function applyRoundDamageAndScheduleNext() {
    const m1 = p1Move;
    const m2 = p2Move;
    const cmp = compareRps(m1, m2);
    let damageToP1 = 0;
    let damageToP2 = 0;
    let winner = null;

    if (cmp === 0) {
      winner = 'tie';
    } else if (cmp === 1) {
      winner = 'p1';
      const atk = atkForCharacter(p1Char, m1);
      const mul = mulForSkill(p1SkillBuff, m1);
      damageToP2 = Math.max(0, Math.floor(atk * mul));
    } else {
      winner = 'p2';
      const atk = atkForCharacter(p2Char, m2);
      const mul = mulForSkill(p2SkillBuff, m2);
      damageToP1 = Math.max(0, Math.floor(atk * mul));
    }

    // 技能回血/扣血與傷害一起在結果公布時套用
    const p1Heal = Number(p1SkillBuff?.hp_heal) || 0;
    const p2Heal = Number(p2SkillBuff?.hp_heal) || 0;
    p1Hp = clampHp(p1Hp + p1Heal - damageToP1, p1Max);
    p2Hp = clampHp(p2Hp + p2Heal - damageToP2, p2Max);

    let matchWinner = null;
    if (p1Hp <= 0 && p2Hp <= 0) matchWinner = 'tie';
    else if (p1Hp <= 0) matchWinner = 'p2';
    else if (p2Hp <= 0) matchWinner = 'p1';

    lastResult = {
      winner,
      p1Move: m1,
      p2Move: m2,
      damageToP1,
      damageToP2,
      p1Heal,
      p2Heal,
      matchWinner,
    };

    phase = Phase.SHOW_RESULT;
    emit();

    clearResultTimer();
    // 終局：自動進入 MATCH_OVER（讓玩家看到冠軍畫面）
    // 一般回合結束：停在 SHOW_RESULT，等待 P1 感應任一卡片才進下一回合
    if (matchWinner) {
      resultTimer = setTimeout(() => {
        resultTimer = null;
        phase = Phase.MATCH_OVER;
        emit();
      }, MS_POST_RESULT);
    }
  }

  /** 結算畫面感應卡片：進入下一回合的 TURN_P1，並嘗試把此卡帶入 */
  function handleResultAdvance(card) {
    clearResultTimer();
    // 安全網：終局時不應走此路徑（讓自動 timer 收尾即可）
    if (p1Hp <= 0 || p2Hp <= 0) {
      phase = Phase.MATCH_OVER;
      emit();
      return true;
    }

    // 進入下一回合的 TURN_P1（清空上回合狀態）
    p1SkillBuff = p2SkillBuff = null;
    p1Move = p2Move = null;
    lastResult = null;
    round += 1;
    phase = Phase.TURN_P1;
    emit();

    const type = card?.type;
    if (type === 'SKILL' || type === 'RPS') {
      // 延遲短暫時間讓 UI 顯示 TURN_P1 畫面後再套用卡片
      resultTimer = setTimeout(() => {
        resultTimer = null;
        if (phase === Phase.TURN_P1) handleTurnCard(card, 'p1');
      }, MS_RESULT_TO_TURN_P1);
    } else {
      // CHARACTER / UNKNOWN：只用來「結束結算畫面」，無法套用
      toast('此卡無法套用，已進入下一回合（請 P1 出牌）');
    }
    return true;
  }

  /** P2 出拳後：2 秒隱藏 → 揭曉兩側 → 1.25 秒後結算 */
  function startRpsRevealSequence() {
    clearResultTimer();
    phase = Phase.RPS_PENDING_REVEAL;
    emit();

    resultTimer = setTimeout(() => {
      resultTimer = null;
      phase = Phase.RPS_REVEALED;
      emit();

      resultTimer = setTimeout(() => {
        resultTimer = null;
        applyRoundDamageAndScheduleNext();
      }, MS_REVEAL_TO_DAMAGE);
    }, MS_REVEAL_HIDDEN);
  }

  function handleTurnCard(card, who) {
    const type = card?.type;
    const isP1 = who === 'p1';

    if (type === 'SKILL') {
      const alreadyUsed = isP1 ? p1SkillBuff : p2SkillBuff;
      if (alreadyUsed) {
        toast(`${isP1 ? 'P1' : 'P2'} 本回合已使用過技能，請感應出拳卡。`);
        return false;
      }
      const buff = cloneSkillBuff(card);
      // 回血/扣血延到結果公布時才套用，避免血條變動洩漏技能資訊
      if (isP1) {
        p1SkillBuff = buff;
      } else {
        p2SkillBuff = buff;
      }
      emit();
      return true;
    }

    if (type === 'RPS') {
      const m = card.rps;
      if (!m || m === 'unknown') {
        toast('出拳卡資料異常。');
        return false;
      }
      const cardPlayer = card.player ?? 0;
      if (cardPlayer !== 0) {
        const expected = isP1 ? 1 : 2;
        if (cardPlayer !== expected) {
          toast(`這是 P${cardPlayer} 的出拳卡，請 ${isP1 ? 'P1' : 'P2'} 使用 P${expected} 的出拳卡。`);
          return false;
        }
      }
      if (isP1) {
        p1Move = m;
        phase = Phase.TURN_P2;
        emit();
      } else {
        p2Move = m;
        startRpsRevealSequence();
      }
      return true;
    }

    toast('此階段請感應「技能卡」或「出拳卡」。');
    return false;
  }

  function onCardRead(card) {
    const type = card?.type;

    if (phase === Phase.LOBBY || phase === Phase.MATCH_OVER) {
      toast('請先按「開始本場」');
      return false;
    }

    // 結算畫面：任一卡片（含 UNKNOWN）都會推進到下一回合
    if (phase === Phase.SHOW_RESULT) {
      return handleResultAdvance(card);
    }

    if (!type || type === 'UNKNOWN') {
      toast('無法辨識此卡，請先在管理器寫入資料。');
      return false;
    }

    if (phase === Phase.WAIT_P1_CHAR) {
      if (type !== 'CHARACTER') {
        toast('此階段請感應「角色卡」。');
        return false;
      }
      p1Char = { ...card };
      p1Max = p1Hp = clampHp(card.hp ?? 100, 999);
      phase = Phase.WAIT_P2_CHAR;
      emit();
      return true;
    }

    if (phase === Phase.WAIT_P2_CHAR) {
      if (type !== 'CHARACTER') {
        toast('此階段請感應「角色卡」。');
        return false;
      }
      if (card.uid && p1Char?.uid && card.uid === p1Char.uid) {
        toast('P2 請使用與 P1 不同的角色卡。');
        return false;
      }
      p2Char = { ...card };
      p2Max = p2Hp = clampHp(card.hp ?? 100, 999);
      round = 1;
      phase = Phase.TURN_P1;
      emit();
      return true;
    }

    if (phase === Phase.TURN_P1) return handleTurnCard(card, 'p1');
    if (phase === Phase.TURN_P2) return handleTurnCard(card, 'p2');

    if (
      phase === Phase.RPS_PENDING_REVEAL ||
      phase === Phase.RPS_REVEALED
    ) {
      toast('結算中，請稍候…');
      return false;
    }

    return false;
  }

  function dispose() {
    clearResultTimer();
  }

  return {
    Phase,
    getSnapshot: snapshot,
    enterArcade,
    startMatch,
    onCardRead,
    dispose,
  };
}
