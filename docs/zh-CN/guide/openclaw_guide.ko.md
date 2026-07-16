
# N.E.K.O.를 QwenPaw에 연결하기

N.E.K.O.는 기존 설정과의 호환성을 위해 QwenPaw 통합을 계속 **OpenClaw**라고 부릅니다. 이 안내서의 OpenClaw 스위치는 별도로 실행 중인 QwenPaw 서비스에 연결됩니다.

## 1. 출처 확인 및 설치

현재 절차는 [QwenPaw 공식 저장소](https://github.com/agentscope-ai/QwenPaw)에서 확인하세요. 아래 명령은 원격 설치 스크립트를 다운로드해 바로 실행합니다. 보안 정책에서 요구한다면 먼저 스크립트를 검토하세요. 제한된 네트워크나 관리형 장치에서는 설치가 차단될 수 있습니다.

macOS / Linux:

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

설치 프로그램은 `uv`, 격리 환경, QwenPaw 및 종속성을 준비합니다. 설치 후 새 터미널을 여세요.

## 2. 초기화

```bash
qwenpaw init --defaults
```

동의하기 전에 QwenPaw의 보안 경고를 읽으세요. 하나의 로컬 인스턴스는 실행 계정이 사용할 수 있는 파일, 명령 및 자격 증명에 접근할 수 있습니다. 서로 신뢰하지 않는 사용자가 인스턴스를 공유하지 않도록 하세요.

![QwenPaw 초기화 보안 알림](assets/openclaw_guide/image1.png)

## 3. 시작 및 확인

```bash
qwenpaw app
```

기본 콘솔 주소는 `http://127.0.0.1:8088/`입니다. 터미널을 계속 실행한 상태로 브라우저에서 주소를 여세요. 페이지가 열리지 않으면 N.E.K.O.를 활성화하기 전에 QwenPaw 시작 오류부터 해결하세요.

인증과 네트워크 경계를 이해하고 설정하기 전에는 localhost 밖으로 서비스를 노출하지 마세요.

## 4. QwenPaw에서 모델 설정

QwenPaw 콘솔의 모델 페이지에서 provider를 선택하고 필요한 자격 증명을 입력해 저장합니다. 그런 다음 채팅 페이지에서 설정한 모델을 선택하세요. 사용 가능한 provider와 모델 이름은 설치된 QwenPaw 버전에 따라 달라지므로 복사된 목록 대신 현재 UI를 확인하세요.

![QwenPaw 모델 설정](assets/openclaw_guide/image2.png)

## 5. 선택 사항: 실행자 persona

함께 제공되는 [교체 archive](assets/openclaw_guide/qwenpaw-executor-profile.zip)에는 실행자 지향 `SOUL.md`, `AGENTS.md`, `PROFILE.md`가 들어 있습니다. 이 단계는 연결에 필요하지 않으며 QwenPaw 동작을 변경합니다.

교체하기 전에:

1. QwenPaw를 중지하고 `.qwenpaw/workspaces/default`를 백업합니다.
2. archive를 검사하고 현재 workspace와 비교합니다.
3. 교체하려는 파일만 복사합니다.

기본 설정 directory는 보통 Windows의 `%USERPROFILE%\.qwenpaw` 또는 macOS/Linux의 `~/.qwenpaw`입니다. `BOOTSTRAP.md` 삭제는 이 선택적 실행자 프로필 절차의 일부일 뿐이며 N.E.K.O. 연결에는 필요하지 않습니다. 변경 후 `qwenpaw app`을 다시 시작하세요.

## 6. N.E.K.O.에서 활성화

1. QwenPaw를 시작하고 계속 실행합니다.
2. N.E.K.O.의 paw/Agent 패널을 엽니다.
3. Agent master 스위치를 켭니다.
4. **OpenClaw** 하위 스위치를 켭니다.
5. 가용성 검사를 기다립니다.

N.E.K.O.의 기본 주소는 `http://127.0.0.1:8088`입니다. QwenPaw가 다른 주소를 사용한다면 N.E.K.O. core 설정에서 `openclawUrl`을 변경한 뒤 다시 시도하세요. Adapter는 `qwenpawUrl`도 인식합니다.

현재 adapter는 QwenPaw v2 console API와 이전 agent-compatible API를 모두 인식합니다. 가용성 검사는 버전에 맞춰 `/api/version` 또는 `/api/agent/health`를 확인하고, 이후 일치하는 console 또는 agent endpoint를 사용합니다. 기본 console 구성에는 별도 channel 파일이 필요하지 않습니다.
