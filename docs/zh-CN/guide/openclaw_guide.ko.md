# NEKO로 QwenPaw 연결하기

## QwenPaw 설치 가이드

### 1단계: 설치

Python 을 수동으로 설정할 필요가 없습니다. 한 줄 명령으로 `uv` 설치, 가상 환경 생성, QwenPaw 및 의존성 설치까지 자동으로 완료됩니다. 단, 일부 네트워크 환경이나 기업 권한 정책에서는 사용할 수 없을 수 있습니다.

macOS / Linux:

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

### 2단계: 초기화

설치가 끝나면 새 터미널을 열고 다음 명령을 실행하세요.

```bash
qwenpaw init --defaults
```

초기화 과정에서는 보안 경고가 표시됩니다. QwenPaw 는 로컬 환경에서 실행되며, 같은 인스턴스를 여러 사람이 공유하면 파일, 명령, 비밀 정보 접근 권한도 함께 공유된다고 안내합니다. 내용을 확인한 뒤 `yes` 를 입력해 계속 진행하세요.

![QwenPaw 초기화 보안 경고](assets/openclaw_guide/image1.png)

### 3단계: 실행

```bash
qwenpaw app
```

정상적으로 시작되면 터미널 마지막 줄에 보통 다음이 표시됩니다.

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

시작 후 `http://127.0.0.1:8088` 에 접속하면 QwenPaw 콘솔을 열 수 있습니다.

### 4단계: 페르소나 파일 교체 (선택)

초기화가 끝나면 QwenPaw 는 설정 디렉터리를 자동으로 만듭니다.

- Windows 기본 경로: `C:\Users\사용자이름\.qwenpaw`
- macOS 기본 경로: `~/.qwenpaw`

`.qwenpaw` 는 숨김 폴더이므로 필요하면 표시를 켜야 합니다.

- Windows: 파일 탐색기에서 숨김 항목 표시
- macOS: Finder 에서 `Command + Shift + .`

QwenPaw 를 N.E.K.O 용의 순수한 백엔드 실행기로 쓰고 싶다면 아래 교체 파일을 내려받으세요.

- [교체 파일.zip](assets/openclaw_guide/替换文件.zip)

압축 파일 안의 `SOUL.md`, `AGENTS.md`, `PROFILE.md` 를 `.qwenpaw/workspaces/default` 에 복사해 덮어쓰고, 그 디렉터리의 `BOOTSTRAP.md` 는 삭제하세요.

그다음 `CTRL+C` 로 QwenPaw 를 멈춘 뒤 다시 실행합니다.

```bash
qwenpaw app
```

## 기본 설정: 모델 설정

QwenPaw 콘솔을 열고 모델 페이지로 이동한 다음 사용할 제공자를 선택하세요. 초보자에게는 `DashScope` 가 가장 흔한 선택이지만, API Key 에 따라 다른 제공자를 사용해도 됩니다.

설정을 열고 API Key 를 입력한 뒤 저장하세요.

![QwenPaw 모델 설정 화면](assets/openclaw_guide/image2.png)

저장 후 채팅 화면으로 돌아가면 설정한 모델을 선택할 수 있습니다.

## N.E.K.O 에서 OpenClaw 활성화하기

N.E.K.O 내부 이름은 여전히 `openclaw` 이므로, UI 에 보이는 `OpenClaw` 토글은 곧 QwenPaw 를 의미합니다.

다음 순서로 진행하세요.

1. N.E.K.O 의 Agent 패널을 엽니다
2. 먼저 `Agent` 메인 스위치를 켭니다
3. `openclawUrl` 이 `http://127.0.0.1:8088` 을 가리키는지 확인합니다
4. 그다음 `OpenClaw` 하위 스위치를 켭니다
5. 사용 가능 여부 확인이 통과될 때까지 기다립니다

N.E.K.O 는 먼저 QwenPaw 호환 엔드포인트를 시도하고, 필요하면 자동으로 `process` 엔드포인트로 폴백합니다. 메인 연결 경로에서는 커스텀 채널 설정이 필요하지 않습니다.
