# Modo caçador do PS5: plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** até 01/12/2026, oferta de grupo não confirmada abaixo do alvo toca com som e a guarda do workflow cai para 7 min; depois volta sozinho (sem som, guarda 25 min). PS5 Pro sai dos alertas.

**Architecture:** uma data em `config.py` e uma função `cacador(agora)` em `monitor.py` decidem as duas coisas; o workflow pergunta a `monitor.guarda_min(agora)` em vez de usar 12 fixo.

**Tech Stack:** Python 3 só com biblioteca padrão; GitHub Actions; auto-checagem `python3 ps5/monitor.py --teste`.

**Spec:** `docs/superpowers/specs/2026-11-20-ps5-modo-cacador-design.md`

## Global Constraints
- Só biblioteca padrão; nomes e comentários em português.
- `CACADOR_ATE = date(2026, 12, 1)` (exclusivo: 01/12 já é modo normal).
- Guarda: 7 min no modo caçador, 25 min fora dele.
- `MODELOS_COM_ALERTA = ['leitor', 'digital']`; alvos inalterados (leitor 3799, digital 3499).
- Não confirmada **acima** do alvo (só R$ 150 abaixo da confirmada) continua sem som, em qualquer modo.

## Review Focus
- Virada da data: `agora` em 30/11 23:59 é caçador, 01/12 00:00 não é (a data compara no fuso de Brasília, que é o de `agora`).
- `estado.json` sem `ultima` ou ilegível: a guarda não pula (comportamento atual mantido).
- Mesma oferta não confirmada não toca duas vezes em 24 h (regra `recente('nc|…')` atual, inalterada).
- Pro abaixo do alvo não gera alerta, mas continua no resumo e no painel.

---

### Task 1: regra de som e janela do modo caçador

**Files:**
- Modify: `ps5/config.py` (adicionar `CACADOR_ATE`; `MODELOS_COM_ALERTA` sem `'pro'`)
- Modify: `ps5/monitor.py:570-575` (`decidir_alertas`), novas `cacador` e `guarda_min` perto dela
- Test: auto-checagem em `ps5/monitor.py` (bloco "grupos do Telegram", ~linha 1040)

**Interfaces:**
- Produces: `cacador(agora: datetime) -> bool` (`agora.date() < config.CACADOR_ATE`); `guarda_min(agora: datetime) -> int` (7 ou 25).

- [ ] **Step 1: ajustar a auto-checagem**
  - No cenário `so_um` (agora = 13/11, 3700 ≤ alvo 3799): `assert [(a['modelo'], a['som']) for a in d['alertas']] == [('leitor', True)] and not enviadas[-1][1]`.
  - Repetir `so_um` numa pasta nova com `config.CACADOR_ATE = date(2026, 11, 1)` (restaurar no `finally`): espera `[('leitor', False)]` e `enviadas[-1][1]` verdadeiro.
  - `assert cacador(datetime(2026,11,30,23,59,tzinfo=fontes.BRT)) and not cacador(datetime(2026,12,1,0,0,tzinfo=fontes.BRT))`
  - `assert guarda_min(datetime(2026,11,27,tzinfo=fontes.BRT)) == 7 and guarda_min(datetime(2026,12,2,tzinfo=fontes.BRT)) == 25`
- [ ] **Step 2:** `python3 ps5/monitor.py --teste` → falha (`cacador` não existe / som False).
- [ ] **Step 3:** implementar `cacador`, `guarda_min`, `CACADOR_ATE`; em `decidir_alertas`, `'som': cacador(agora) and d['preco'] <= config.ALVOS.get(m, 0)`; tirar `'pro'` de `MODELOS_COM_ALERTA`.
- [ ] **Step 4:** `python3 ps5/monitor.py --teste` → termina com "OK: auto-checagem passou".
- [ ] **Step 5:** commit.

### Task 2: guarda do workflow e gatilho

**Files:**
- Modify: `.github/workflows/ps5.yml` (passo "Pular se outra rodada acabou de terminar")

**Interfaces:**
- Consumes: `monitor.guarda_min(agora)` da Task 1.

- [ ] **Step 1:** no script da guarda, `sys.path.insert(0, 'ps5'); import monitor` e trocar `12 * 60` por `monitor.guarda_min(dt.datetime.now(ultima.tzinfo)) * 60`; atualizar o comentário.
- [ ] **Step 2:** verificar localmente o script da guarda com um `estado.json` de 8 min atrás → `pular=false`; de 5 min atrás → `pular=true`.
- [ ] **Step 3:** commit, push da branch, rodar "Buscador PS5" na branch (não manda Telegram fora da main) e conferir que passa.
- [ ] **Step 4 (depois do OK do dono e do merge):** gatilho do cron-job.org de 30 para 10 min.
