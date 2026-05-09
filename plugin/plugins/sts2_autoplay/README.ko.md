# 빠른 시작

`sts2_autoplay`는 `STS2 AI Agent`가 노출하는 로컬 《Slay the Spire 2》 상태를 N.E.K.O에 연결하는 데 사용됩니다. 플러그인은 보드를 읽고, 합법적인 액션을 실행하며, 전략에 따라 자동 플레이하고, 네코가 단일 카드를 선택하게 하며, 프런트엔드에 관찰 정보를 푸시하고, 백그라운드 작업에서 네코가 소프트 가이던스를 보내 다음 결정 라운드에 영향을 미치도록 허용합니다.

## 사용 튜토리얼

### MOD 가져오기

Git Clone 사용:
```text
git clone https://github.com/CharTyr/STS2-Agent.git
```

### 게임 Mod 설치

Steam에서 *Slay the Spire 2*를 마우스 오른쪽 버튼으로 클릭하고 "관리 -> 로컬 파일 찾아보기"를 선택할 수 있습니다.

Steam 기본 게임 디렉터리는 일반적으로 다음과 비슷합니다:

```text
...\Steam\steamapps\common\Slay the Spire 2
```

`STS2 AI Agent` mod를 첨탑 게임 디렉터리의 `mods/` 아래로 복사합니다.

Slay the Spire 2 디렉터리에 mods 폴더가 없으면 직접 만들어 주세요.

```text
mod 사용으로 인해 저장 데이터가 손실될 수 있습니다. 백업하거나 콘솔로 복구하세요(첨탑 메인 메뉴에서 "~" 키를 누르고 "unlock all"을 입력하면 모든 캐릭터와 난이도를 잠금 해제할 수 있습니다).
```

설치 완료 후 디렉터리는 다음과 같아야 합니다:

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### 게임 실행 및 인터페이스 확인

먼저 정상적으로 게임을 시작하여 Mod가 게임과 함께 로드되도록 합니다.

mod 로드 후 NEKO에서 고양이 발을 활성화하고, 플러그인을 켜고, 플러그인 패널에 진입하여 수동으로 Slay the Spire 플러그인을 시작합니다.

### 사용 가능한 명령

【카드 내】【자동 플레이】【런 클리어】【잘 플레이하고 있어?】【정지】
【카드 한 장 내】【어떤 카드 내】【카드 한 장 추천】 ... 등등...

## 기능 개요

- 로컬 `STS2 AI Agent` HTTP 서비스에 연결하여 게임 상태를 읽음.
- 수동 한 단계 실행, 백그라운드 반자동 플레이, 일시 중지, 재개 및 정지 지원.
- 세 가지 결정 모드 지원: `full-program`, `half-program`, `full-model`.
- 캐릭터별 전략 문서 로드 지원, 전략 파일은 `strategies/`에 위치.
- 네코 단일 카드 선택 지원: 현재 낼 수 있는 `play_card` 액션에서만 한 장 선택, 먼저 이유를 푸시한 다음 실행.
- 네코 소프트 가이던스 지원: 사용자 또는 네코가 자연어 가이던스를 보낼 수 있으며, 다음 LLM 결정 라운드에서 참조됨.
- 백그라운드 관찰 보고 지원: 현재 층, 전투, 손패, 적 의도, LLM 이유 등을 프런트엔드로 푸시.
- 안전 보호 지원: 저체력 일시 중지, 보스/위험 공격 시 감속, 체력 회복 후 자동 재개, 빈사 생존 전략, 이익 최대화 및 시너지 점수.

## 종속성

이 플러그인은 업스트림 Mod `STS2 AI Agent`가 제공하는 로컬 HTTP 서비스에 의존합니다:

- 게임 내 Mod: `STS2AIAgent`
- 기본 로컬 인터페이스 주소: `http://127.0.0.1:8080`
- 헬스 체크 주소: `http://127.0.0.1:8080/health`

즉, 이 플러그인이 작동하려면:

1. 《Slay the Spire 2》에 `STS2 AI Agent` Mod가 설치되어 있어야 합니다.
2. 게임 시작 후 `http://127.0.0.1:8080/health`에 접근할 수 있어야 합니다.
3. N.E.K.O에서 `sts2_autoplay` 플러그인이 활성화되어 있어야 합니다.

## 본 플러그인 구성

구성 파일: `plugin.toml`

### 기본 구성

| 구성 항목 | 기본값 | 설명 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | 첨탑 로컬 Agent 주소. |
| `connect_timeout_seconds` | `5` | 연결 시간 초과 초. |
| `request_timeout_seconds` | `15` | 요청 시간 초과 초. |
| `poll_interval_idle_seconds` | `3` | 유휴 상태 폴링 간격. |
| `poll_interval_active_seconds` | `1` | 자동 플레이 실행 시 폴링 간격. |
| `action_interval_seconds` | `1.5` | 각 액션 간 추가 간격. |
| `post_action_delay_seconds` | `0.5` | 액션 실행 후 보드 안정화를 기다리는 간격. |
| `autoplay_on_start` | `false` | 플러그인 시작 후 자동으로 플레이 시작 여부. |
| `semi_auto_autoplay` | `true` | 자동 플레이 시작 시 반자동 작업 컨텍스트 생성 여부. |
| `mode` | `half-program` | 현재 자동 플레이 모드. |
| `character_strategy` | `defect` | 캐릭터 전략 이름, `strategies/<name>.md`에 해당. |
| `max_consecutive_errors` | `3` | 최대 연속 오류 횟수, 초과 시 연결 끊김으로 간주. |
| `push_notifications` | `true` | 기록 보존 필드. |
| `event_stream_enabled` | `false` | 예약 필드, 현재 실제로 활성화되지 않음. |

### 결정 모드

`mode`는 다음 값을 지원하며, 해당 중국어 별칭도 지원합니다:

| 모드 | 중국어 별칭 | 설명 |
| --- | --- | --- |
| `full-program` | `全程序` | 순수 프로그램 휴리스틱, 모델 호출 없음. |
| `half-program` | `半程序` | 먼저 프로그램으로 사전 검사, 그런 다음 한 번 모델 결정 호출, 합법성 검증/대체 수행. |
| `full-model` | `全模型` | 두 번의 모델 호출: 먼저 reasoning, 그런 다음 final action; 중간에 프로그램 검사, 최종적으로 합법성 검증. |

### 캐릭터 전략

`character_strategy`는 `strategies/<name>.md`에 따라 전략 문서를 검색합니다. 현재 내장된 전략:

- `defect`
- `ironclad`
- `silent_hunter`
- `necrobinder`
- `regent`

`strategies/`에 Markdown 파일을 추가하여 전략을 확장할 수 있습니다. 예:

```text
strategies/my_strategy.md
```

그런 다음 구성 또는 진입점 매개변수를 다음과 같이 설정합니다:

```text
my_strategy
```

### 프런트엔드 푸시 및 네코 관찰

| 구성 항목 | 기본값 | 설명 |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | 자동 플레이 액션/오류를 프런트엔드로 능동적으로 푸시할지 여부. |
| `llm_frontend_output_probability` | `0.15` | 일반 액션 푸시 확률, 범위는 `0.0 ~ 1.0`로 수렴됨. 오류는 강제 푸시. |
| `neko_reporting_enabled` | `true` | 네코 관찰 보고서를 푸시할지 여부. |
| `neko_report_interval_steps` | `1` | 자동 플레이 N 단계마다 관찰 보고서 푸시, 최소 `1`. |
| `neko_commentary_enabled` | `true` | 관찰 보고서에서 네코 실시간 해설을 생성할지 여부. 비활성화해도 구조화된 관찰 보고서는 푸시되지만 `live_commentary.text`는 비어 있게 됩니다. |
| `neko_commentary_probability` | `0.65` | 일반 저우선순위 해설의 트리거 확률, 범위는 `0.0 ~ 1.0`로 수렴됨; 저체력, 처형, 고공격 등 고우선순위 시나리오는 확률을 우회할 수 있음. |
| `neko_commentary_min_interval_seconds` | `4` | 동일한 저우선순위 시나리오에서 해설을 반복하는 최소 간격 초, 스팸과 반복을 줄이기 위해 사용. |
| `neko_critical_commentary_always` | `true` | `critical` / `high` 긴급도 해설을 항상 방송할지 여부, 예: 빈사, 처형, 적의 고공격. |
| `neko_guidance_max_queue` | `50` | 네코 소프트 가이던스 큐 최대 길이. |

네코 관찰 보고서는 간소화된 `report`, `neko_context`, `live_commentary`, `task` 등 metadata를 포함하여 프런트엔드나 대화 로직이 이를 "프로세스 관찰"로 인식하도록 도우며, 작업 완료 알림이 아닙니다. 사용자 토큰을 절약하기 위해 푸시 내용은 현재 액션, 체력, 손패, 적, 전술 요약, 소비된 가이던스, 작업 요약만 유지합니다.

`live_commentary`는 프런트엔드/TTS에 짧은 음성 필드를 제공합니다: `text`, `scene`, `mood`, `urgency`, `priority`, `tts`, `interrupt`, `tone`, `character_strategy`. 해설은 시나리오별로 템플릿 풀에서 무작위로 선택되어 반복을 줄이며, 캐릭터 전략에 따라 경향을 조정합니다, 예: `defect`은 이성적, `ironclad`는 안정적. 현재 빈사, 저체력, 처형, 적의 공격, 방어, 일반 전투, 보상, 상점, 휴식처, 이벤트, 맵, 그리고 전투 종료, 핵심 유물, 경로 선택 완료 등 이벤트 수준 해설을 다룹니다.

### 안전 보호 및 자율 액션

| 구성 항목 | 기본값 | 설명 |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | 현재 체력 비율이 이 값보다 낮으면 백그라운드 자동 플레이가 자율적으로 일시 중지됩니다. |
| `neko_auto_safe_hp_threshold` | `0.5` | 체력이 이 비율로 회복되면 자동 재개 가능. |
| `neko_auto_dangerous_attack_threshold` | `20` | 적의 공격 데미지가 이 값에 도달하고 방어를 뚫을 수 있을 때 자동 감속. |
| `neko_auto_resume_after_low_hp` | `true` | 저체력 일시 중지 후 체력 회복 시 자동 재개를 허용할지 여부. |
| `neko_desperate_enabled` | `true` | 빈사 생존 전략을 활성화할지 여부. |
| `neko_desperate_hp_threshold` | `0.2` | 빈사 생존 전략을 트리거하는 체력 비율. |
| `neko_maximize_enabled` | `true` | 이익 최대화 카드 선택을 활성화할지 여부. |
| `neko_synergy_enabled` | `true` | 시너지/협동 점수를 활성화할지 여부. |

현재 자율 액션에는 다음이 포함됩니다:

- `pause`: 저체력 시 일시 중지하고 사용자 또는 네코의 지시를 기다림.
- `slow_down`: 보스전 또는 위험 공격 시 액션 간격을 일시적으로 느리게.
- `resume`: 안전 체력 조건 충족 후 재개.

## 일반 사용자 추천 표현

일반 사용자는 아래의 저수준 진입점을 기억할 필요가 없습니다. 우선 사용자의 원문을 `sts2_neko_command`에 전달하고, 플러그인 내부에서 상태 확인, 조언 제공, 실제 카드 내기, 한 단계 실행, 자동 플레이 시작, 일시 중지, 재개, 정지, 최근 카드 내기 복기, 자동 플레이 질문 답변, 또는 자동 플레이 중 소프트 가이던스로 사용할지 판단합니다.

추천 상호작용 규칙:

| 사용자 표현 | 플러그인 동작 |
| --- | --- |
| `첨탑 연결됐어?` / `지금 무슨 상황이야?` | 연결, 상태 또는 스냅샷만 확인, 게임 조작 없음. |
| `이번 턴 어떻게 해?` / `어떤 카드가 좋아?` | 낼 수 있는 카드 한 장만 추천하고 이유 설명, 자동으로 카드 내지 않음. |
| `카드 한 장 내줘` / `카드 한 장 골라서 내` | 명확한 권한 부여 후, `play_card` 액션에서 한 장만 골라 냄. |
| `한 단계 해줘` / `한 단계 실행` | 명확한 권한 부여 후 합법적인 한 액션 실행, 턴 종료, 보상 선택 또는 맵 이동을 포함할 수 있음. |
| `이 층 해줘` / `자동으로 좀 해` | 반자동 플레이 시작, 기본적으로 현재 층 완료를 정지 조건으로. |
| `먼저 방어해` / `딜 욕심내지 마` | 자동 플레이 실행 중에는 다음 결정 라운드에 진입하는 소프트 가이던스로; 실행되지 않을 때는 보수적으로 명확화를 요청, 임의로 실행하지 않음. |
| `방금 어땠어?` / `방금 그 카드 복기해봐` | 최근 가벼운 스냅샷에 따라 플레이 감 평가 제공, 게임 조작 없음. |
| `왜 그렇게 해?` / `뭐 하고 있어?` | 자동 플레이 실행 중 현재 전략과 보드 근거에 답변, 추가 작업 없음. |
| `잠깐 멈춰` / `계속해` / `그만하자` | 각각 자동 플레이를 일시 중지, 재개 또는 정지. |

안전 기본값: 상담은 작업하지 않음, 모호한 표현은 위험한 액션을 실행하지 않음; 사용자가 명확히 "해줘", "실행", "자동 플레이", "맡겨"라고 말할 때만 실제로 작업합니다.

## 플러그인 진입점

다음 진입점들은 호스트에 노출되어 있으며, N.E.K.O에서 직접 호출할 수 있습니다. 일반 사용자 시나리오에서는 `sts2_neko_command` 우선 호출을 추천하며, 다른 진입점은 주로 개발자용 정밀 제어 인터페이스입니다.

### `sts2_neko_command`

Slay the Spire 자연어 마스터 진입점. 사용자가 저수준 도구를 명시적으로 지정하지 않은 경우 우선적으로 호출합니다.

매개변수:

- `command`: 필수, 사용자 원문. 예: `이번 턴 어떻게 해?`, `카드 한 장 내줘`, `먼저 방어해`, `잠깐 멈춰`.
- `scope`: 선택, 기본 `auto`. 가능한 값: `auto`, `status`, `advice`, `one_card`, `one_action`, `autoplay`, `control`, `guidance`, `review`, `question`, `chat`.
- `confirm`: 선택, 기본 `false`. 지속적 위임 등 고위험 작업 확인에 사용.

반환에는 `intent`, `action`, `executed`, `needs_confirmation`, `summary` 및 기저 `result`가 포함됩니다.

### `sts2_health_check`

로컬 첨탑 Agent 서비스가 사용 가능한지 확인합니다.

### `sts2_refresh_state`

현재 첨탑 상태를 강제로 한 번 새로 고칩니다.

### `sts2_get_status`

연결 상태, 자동 플레이 상태, 현재 모드, 캐릭터 전략, 반자동 작업, 최근 오류, 최근 액션 등의 정보를 가져옵니다.

### `sts2_get_snapshot`

최근 캐시된 게임 스냅샷과 현재 실행 가능한 액션을 가져옵니다.

### `sts2_step_once`

현재 전략에 따라 한 단계 실행합니다.

### `sts2_play_one_card_by_neko`

네코가 카드를 선택하고 내도록 합니다.

매개변수:

- `objective`: 선택, 사용자 권한 부여 목표. 예: `카드 한 장 골라서 내`.

동작:

1. 현재 플레이어, 손패, 적 및 합법 액션을 읽음.
2. `play_card` 액션만 유지.
3. 현재 모드/전략이 카드 한 장을 선택하게 함.
4. 먼저 프런트엔드에 "어떤 카드를 낼 예정인지와 이유"를 푸시.
5. 액션이 여전히 합법인지 재검증.
6. 카드를 내고 완료 관찰을 푸시.

현재 낼 수 있는 카드가 없으면 `idle`을 반환하고 실패 이유를 푸시합니다.

### `sts2_start_autoplay`

백그라운드 반자동 플레이 루프를 시작합니다.

매개변수:

- `objective`: 선택, 사용자 권한 부여 목표. 예: `이 층 해줘`.
- `stop_condition`: 정지 조건, 기본 `current_floor`.

`stop_condition` 지원:

- `current_floor`: 현재 층 완료 또는 다음 층 진입 후 종료.
- `current_combat` / `combat`: 작업 기간 중 전투에 진입한 후 전투에서 나가면 종료.
- `manual` / `none`: 자동 완료되지 않으며, 수동으로 정지해야 함.

시작 후 플러그인은 반자동 작업 컨텍스트를 만들고 작업 시작 이벤트를 프런트엔드에 푸시합니다. 작업 완료 시 `semi_auto_task_completed`가 푸시됩니다.

### `sts2_pause_autoplay`

자동 플레이를 일시 중지합니다.

### `sts2_resume_autoplay`

일시 중지되었으며 백그라운드 작업이 여전히 존재하는 자동 플레이를 재개합니다. 백그라운드 작업이 더 이상 존재하지 않으면 안전하게 `idle`을 반환하며 자동 플레이를 암묵적으로 다시 시작하지 않습니다.

### `sts2_stop_autoplay`

자동 플레이를 정지하고 반자동 작업 컨텍스트를 지웁니다.

### `sts2_get_history`

최근 액션 및 상태 기록을 가져옵니다.

매개변수:

- `limit`: 반환 항목 수, 기본 `20`, 범위는 `1 ~ 100`로 제한.

### `sts2_send_neko_guidance`

백그라운드 자동 플레이로 네코 소프트 가이던스를 보냅니다. 가이던스는 큐에 들어가 다음 LLM 결정 라운드 때 컨텍스트에 주입됩니다.

매개변수:

- `content`: 필수, 자연어 가이던스 내용. 예: `먼저 방어해, 딜 서두르지 마`.
- `step`: 선택, 해당 단계 수.
- `type`: 선택, 기본 `soft_guidance`.

### `sts2_set_mode`

자동 플레이 모드를 설정합니다.

매개변수:

- `mode`: `full-program` / `全程序`, `half-program` / `半程序`, `full-model` / `全模型` 지원.

### `sts2_set_character_strategy`

캐릭터 전략 이름을 설정합니다.

매개변수:

- `character_strategy`: 이름 정규화 후 `strategies/<name>.md`에 매칭. 예: `defect`은 `strategies/defect.md`에 매칭.

### `sts2_set_speed`

속도 매개변수를 설정하고 로컬 `plugin.toml`에 다시 씁니다.

매개변수:

- `action_interval_seconds`
- `post_action_delay_seconds`
- `poll_interval_active_seconds`

## 일반적인 사용 방식

### 연결 확인

1. 《Slay the Spire 2》 시작.
2. `http://127.0.0.1:8080/health`에 접근 가능한지 확인.
3. N.E.K.O에서 `sts2_health_check` 호출.

### 수동 한 단계 실행

호출:

```text
sts2_step_once
```

플러그인은 현재 `mode`와 `character_strategy`에 따라 합법적인 액션 하나를 선택하고 실행합니다.

### 네코가 카드 한 장 내게 하기

사용자는 네코에게 다음과 같이 말할 수 있습니다:

```text
카드 한 장 골라서 내
```

호스트는 다음을 호출해야 합니다:

```text
sts2_play_one_card_by_neko
```

플러그인은 현재 낼 수 있는 카드에서만 선택하며, 턴 종료, 맵, 보상 또는 기타 액션은 선택하지 않습니다.

### 네코가 한 층을 클리어하도록

사용자는 다음과 같이 말할 수 있습니다:

```text
이 층 해줘
```

호스트는 다음을 호출해야 합니다:

```text
sts2_start_autoplay
```

추천 매개변수:

```json
{
  "objective": "이 층 해줘",
  "stop_condition": "current_floor"
}
```

작업 실행 중 관찰 이벤트는 단지 진행 보고일 뿐이며 완료를 의미하지 않습니다. 반자동 작업 완료 이벤트를 받았을 때만 사용자에게 이 층이 완료되었다고 알려야 합니다.

### 중간 가이던스

자동 플레이 중 사용자 또는 네코는 가이던스를 보낼 수 있습니다:

```text
먼저 방어해, 데미지 너무 많이 받지 마
```

다음을 호출해야 합니다:

```text
sts2_send_neko_guidance
```

추천 매개변수:

```json
{
  "content": "먼저 방어해, 데미지 너무 많이 받지 마",
  "type": "soft_guidance"
}
```

가이던스는 다음 LLM 결정 라운드 때 참조됩니다. `full-program` 모드는 모델에 의존하지 않으므로 소프트 가이던스의 영향이 제한적입니다.

## 프런트엔드 푸시 이벤트

플러그인은 호스트의 메시지 채널을 통해 다음 종류의 이벤트를 푸시합니다. 작업 시작/완료, 오류 및 단일 카드 예고를 제외하고, 일반 관찰은 사용자 토큰 소비를 줄이기 위해 가능한 한 짧은 텍스트와 간소화된 metadata를 사용합니다.

| 이벤트 유형 | 설명 |
| --- | --- |
| `action` | 일반 자동 플레이 액션 관찰, 확률 제어. |
| `error` | 자동 플레이 오류, 강제 푸시. |
| `neko_report` | 완전한 네코 관찰 보고서, 현재 보드, 손패, 적, 전술 요약 및 모델 이유 포함. |
| `neko_card_task_planned` | 네코 단일 카드 작업이 어떤 카드를 낼 계획. |
| `neko_card_task_completed` | 네코 단일 카드 작업 실행 완료. |
| `neko_card_task_failed` | 네코 단일 카드 작업 실행 불가. |
| `semi_auto_task_started` | 반자동 작업 시작. |
| `semi_auto_task_completed` | 반자동 작업 완료. |
| `neko_autonomous_action` | 시스템 자율 일시 중지, 감속 또는 재개. |

참고: `neko_report`는 프로세스 관찰이며 작업 완료 알림이 아닙니다. 프런트엔드나 대화 로직은 단일 단계 액션, 카드 내기, 턴 종료 또는 상태 새로 고침을 "작업 완료", "보스 처치", "전투 종료" 또는 "런 클리어"라고 표현해서는 안 됩니다. 네코가 다음 결정 라운드에 영향을 주려면 `sts2_send_neko_guidance`를 호출해야 하고, 흐름을 강제 제어하려면 일시 중지, 재개 또는 정지 진입점을 호출해야 합니다.

## 일반 문제 해결

### 플러그인 진입점 호출 시 연결 실패 보고

먼저 확인:

- 게임이 시작되었는지.
- `STS2 AI Agent` Mod가 게임 `mods/`에 올바르게 배치되었는지.
- `http://127.0.0.1:8080/health`에 접근 가능한지.
- `plugin.toml`의 `base_url`이 올바른지.

### `http://127.0.0.1:8080/health`가 열리지 않음

우선 확인:

1. 게임이 실제로 시작되었는지.
2. `STS2AIAgent.dll`, `STS2AIAgent.pck`, `mod_id.json`이 모두 게임 디렉터리 `mods/`에 복사되었는지.
3. 파일 이름이 시스템에 의해 변경되거나, 중복되거나, 잘못된 디렉터리에 놓이지 않았는지.
4. 작업 중인 곳이 Steam 게임 디렉터리이지 업스트림 저장소 디렉터리가 아닌지.
5. 방화벽이나 보안 소프트웨어가 로컬 포트를 차단하는지.

### 자동 플레이는 실행되지만 프런트엔드가 메시지를 받지 못함

확인:

- `llm_frontend_output_enabled`가 `true`인지.
- `llm_frontend_output_probability`가 너무 낮지 않은지.
- `neko_reporting_enabled`가 `true`인지.
- 디버깅 시 `llm_frontend_output_probability`를 먼저 `1`로 설정해 볼 수 있음.
- 호스트 프런트엔드가 플러그인 푸시 메시지를 받았는지.

### 네코 중간 가이던스에 명백한 효과 없음

확인:

- 현재 모드가 `half-program` 또는 `full-model`인지.
- `sts2_send_neko_guidance`가 `ok`를 반환하는지.
- 가이던스 내용이 충분히 구체적인지, 예: "방어 우선", "최저 체력 적 먼저 공격", "포션 보존".
- 현재 합법 액션이 가이던스를 실제로 충족할 수 있는지.

### 반자동 작업이 좀처럼 완료되지 않음

`stop_condition` 확인:

- `manual` / `none`이면 작업이 자동 완료되지 않으며 `sts2_stop_autoplay`를 호출해야 함.
- `current_combat`이면 작업 기간 중 전투에 진입한 후 전투에서 나가면 완료됨.
- `current_floor`이면 일반적으로 현재 층 완료 또는 다음 층 진입 후 완료됨.

`sts2_get_status`를 호출하여 `autoplay.task`를 확인할 수 있습니다.

### 이벤트 룸, 팝업 또는 전환 상태에서 멈춤

현재 버전은 이미 이벤트, 팝업, 전환 상태를 처리했으며, 우선 액션에는 다음이 포함됩니다:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

여전히 멈추면 먼저 `sts2_get_snapshot`으로 현재 `screen`과 `available_actions`를 확인합니다.

### 자동 플레이가 갑자기 일시 중지되거나 느려짐

안전 보호가 트리거되었을 수 있습니다:

- 체력 비율이 `neko_auto_low_hp_threshold`보다 낮으면 일시 중지.
- 보스전이나 위험 공격 시 감속.
- `neko_auto_resume_after_low_hp`가 `true`이면 체력이 `neko_auto_safe_hp_threshold`로 회복되면 자동으로 재개될 수 있음.

`sts2_get_status`를 호출하여 상태를 확인하거나 `sts2_resume_autoplay` / `sts2_stop_autoplay`를 호출하여 처리할 수 있습니다.
