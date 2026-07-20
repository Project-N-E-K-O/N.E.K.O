import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHARACTER_DATA_SCRIPT = (
    PROJECT_ROOT
    / "static"
    / "js"
    / "character_card_manager"
    / "character-data-and-transfer.js"
)


def test_latest_character_card_refresh_wins_when_an_older_request_finishes_last() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the character-card manager browser contract test")

    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(CHARACTER_DATA_SCRIPT))}, 'utf8');
        const pendingCharacterLoads = [];
        const renderedSnapshots = [];

        const document = {{
          readyState: 'loading',
          addEventListener() {{}},
          getElementById() {{ return null; }},
        }};
        const context = {{
          console,
          document,
          Promise,
          Set,
          Map,
          Date,
          Math,
          JSON,
          URL,
          TextEncoder,
          Uint8Array,
          DataView,
          Blob,
          FormData,
          availableModels: [],
          availableVrmModels: [],
          availableMmdModels: [],
          loadCharacterData() {{
            return new Promise(resolve => pendingCharacterLoads.push(resolve));
          }},
          scanModels() {{ return Promise.resolve(true); }},
          loadMasterProfile() {{}},
          renderHiddenCatgirls() {{}},
          expandCharacterCardSection() {{}},
          showMessage() {{}},
          renderCharaCardsView() {{
            renderedSnapshots.push((context.characterCards || []).map(card => card.name));
          }},
          fetch: async (url, options = {{}}) => {{
            if (options.cache !== 'no-store') {{
              throw new Error(`refresh request did not bypass cache: ${{url}}`);
            }}
            const payload = url.endsWith('/character-card/list')
              ? {{ success: true, character_cards: [] }}
              : url.endsWith('/current_catgirl')
                ? {{ current_catgirl: '' }}
                : url.endsWith('/card-faces')
                  ? {{ success: true, names: [] }}
                  : {{ success: true, metas: {{}} }};
            return {{ ok: true, json: async () => payload }};
          }},
          addEventListener() {{}},
          setTimeout(callback) {{ callback(); return 1; }},
          clearTimeout() {{}},
        }};
        context.window = context;
        vm.runInNewContext(source, context);

        (async () => {{
          const olderRefresh = context.loadCharacterCards();
          const importRefresh = context.loadCharacterCards();
          if (pendingCharacterLoads.length !== 2) throw new Error('expected two concurrent refreshes');

          pendingCharacterLoads[1]({{ '猫娘': {{ '刚导入的角色': {{ description: 'fresh' }} }} }});
          await importRefresh;
          pendingCharacterLoads[0]({{ '猫娘': {{ '导入前的旧角色': {{ description: 'stale' }} }} }});
          await olderRefresh;
          await Promise.resolve();

          const finalNames = (context.characterCards || []).map(card => card.name);
          if (JSON.stringify(finalNames) !== JSON.stringify(['刚导入的角色'])) {{
            throw new Error(`stale refresh replaced imported card list: ${{JSON.stringify(finalNames)}}`);
          }}
          if (renderedSnapshots.some(names => names.includes('导入前的旧角色'))) {{
            throw new Error(`stale refresh rendered after import: ${{JSON.stringify(renderedSnapshots)}}`);
          }}
        }})().catch(error => {{
          console.error(error);
          process.exitCode = 1;
        }});
        """
    )

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
