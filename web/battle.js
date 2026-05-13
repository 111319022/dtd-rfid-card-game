/**
 * 單讀卡區雙人對戰狀態機（剪刀石頭布）
 * 平手：雙方本回合不受傷害（0 傷）。
 * 多回合：一方 HP ≤ 0 時結束；每回合依序 P1 技能 → P2 技能 → P1 出拳 → P2 出拳 → 揭曉 → 結算。
 */

export const Phase = {
  LOBBY: 'lobby',
  WAIT_P1_CHAR: 'wait_p1_char',
  WAIT_P2_CHAR: 'wait_p2_char',
  SKILL_P1: 'skill_p1',
  SKILL_P2: 'skill_p2',
  WAIT_P1_RPS: 'wait_p1_rps',
  WAIT_P2_RPS: 'wait_p2_rps',
  RPS_PENDING_REVEAL: 'rps_pending_reveal',
  RPS_REVEALED: 'rps_revealed',
  SHOW_RESULT: 'show_result',
  MATCH_OVER: 'match_over',
};

const MS_REVEAL_HIDDEN = 2000;
const MS_REVEAL_TO_DAMAGE = 1250;
const MS_POST_RESULT = 3200;

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
  if (typeof v !== 'number' || Number.isNaN(v) || v <= 0) return 1;
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

  function snapshot() {
    const reveal = rpsRevealMode(phase);
    const showMovesToUi = reveal === 'moves' || reveal === 'full';

    return {
      phase,
      round,
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
      lastResult,
      rpsReveal: reveal,
      p1Move: showMovesToUi ? p1Move : null,
      p2Move: showMovesToUi ? p2Move : null,
      prompt: promptText(),
      showSkipSkill: phase === Phase.SKILL_P1 || phase === Phase.SKILL_P2,
      skipSkillLabel: phase === Phase.SKILL_P1 ? 'P1 不使用技能' : phase === Phase.SKILL_P2 ? 'P2 不使用技能' : '',
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
        return '請按「開始本場」後，依序感應雙方角色卡。';
      case Phase.WAIT_P1_CHAR:
        return '請 P1 感應角色卡（CHARACTER）';
      case Phase.WAIT_P2_CHAR:
        return '請 P2 感應角色卡（CHARACTER）';
      case Phase.SKILL_P1:
        return '請 P1 感應技能卡，或按「不使用技能」';
      case Phase.SKILL_P2:
        return '請 P2 感應技能卡，或按「不使用技能」';
      case Phase.WAIT_P1_RPS:
        return '請 P1 感應出拳卡（RPS）';
      case Phase.WAIT_P2_RPS:
        return '請 P2 感應出拳卡（RPS）';
      case Phase.RPS_PENDING_REVEAL:
        return '雙方已出拳，揭曉中…';
      case Phase.RPS_REVEALED:
        return '結算中…';
      case Phase.SHOW_RESULT:
        return '本回合結果';
      case Phase.MATCH_OVER:
        return '對戰結束';
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
    phase = Phase.SKILL_P1;
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

    p1Hp = clampHp(p1Hp - damageToP1, p1Max);
    p2Hp = clampHp(p2Hp - damageToP2, p2Max);

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
      matchWinner,
    };

    phase = Phase.SHOW_RESULT;
    emit();

    clearResultTimer();
    resultTimer = setTimeout(() => {
      resultTimer = null;
      if (matchWinner) {
        phase = Phase.MATCH_OVER;
        emit();
      } else {
        beginRoundAfterResult();
      }
    }, MS_POST_RESULT);
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

  function onCardRead(card) {
    const type = card?.type;

    if (phase === Phase.LOBBY || phase === Phase.MATCH_OVER) {
      toast('請先按「開始本場」');
      return false;
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
      phase = Phase.SKILL_P1;
      emit();
      return true;
    }

    if (phase === Phase.SKILL_P1) {
      if (type !== 'SKILL') {
        toast('此階段請感應「技能卡」，或按不使用技能。');
        return false;
      }
      p1SkillBuff = cloneSkillBuff(card);
      let heal = Number(card.hp_heal);
      if (Number.isNaN(heal)) heal = 0;
      p1Hp = clampHp(p1Hp + heal, p1Max);
      phase = Phase.SKILL_P2;
      emit();
      return true;
    }

    if (phase === Phase.SKILL_P2) {
      if (type !== 'SKILL') {
        toast('此階段請感應「技能卡」，或按不使用技能。');
        return false;
      }
      p2SkillBuff = cloneSkillBuff(card);
      let heal2 = Number(card.hp_heal);
      if (Number.isNaN(heal2)) heal2 = 0;
      p2Hp = clampHp(p2Hp + heal2, p2Max);
      phase = Phase.WAIT_P1_RPS;
      emit();
      return true;
    }

    if (phase === Phase.WAIT_P1_RPS) {
      if (type !== 'RPS') {
        toast('此階段請感應「出拳卡」。');
        return false;
      }
      const m = card.rps;
      if (!m || m === 'unknown') {
        toast('出拳卡資料異常。');
        return false;
      }
      p1Move = m;
      phase = Phase.WAIT_P2_RPS;
      emit();
      return true;
    }

    if (phase === Phase.WAIT_P2_RPS) {
      if (type !== 'RPS') {
        toast('此階段請感應「出拳卡」。');
        return false;
      }
      const m = card.rps;
      if (!m || m === 'unknown') {
        toast('出拳卡資料異常。');
        return false;
      }
      p2Move = m;
      startRpsRevealSequence();
      return true;
    }

    if (
      phase === Phase.RPS_PENDING_REVEAL ||
      phase === Phase.RPS_REVEALED ||
      phase === Phase.SHOW_RESULT
    ) {
      toast('結算中，請稍候…');
      return false;
    }

    return false;
  }

  function skipCurrentSkill() {
    if (phase === Phase.SKILL_P1) {
      p1SkillBuff = null;
      phase = Phase.SKILL_P2;
      emit();
      return;
    }
    if (phase === Phase.SKILL_P2) {
      p2SkillBuff = null;
      phase = Phase.WAIT_P1_RPS;
      emit();
      return;
    }
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
    skipCurrentSkill,
    dispose,
  };
}
