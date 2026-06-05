# 把项目根目录加入 sys.path,确保 lib/ adapters/ scripts/ 等包在 CI 和各 pytest
# 调用方式下均可直接 import。(pytest 脚本模式不自动加 CWD;python -m pytest 会加。)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
