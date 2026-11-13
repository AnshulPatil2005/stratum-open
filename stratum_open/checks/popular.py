"""Some of the most-used packages on each registry, for the look-alike check.

A new dependency one typo away from one of these is likely a typosquat. The
lists hold well-known neighbours too (`preact` beside `react`, `tomli` beside
`toml`), so a real package isn't taken for a copy of another. They're a
snapshot of popular names, not a ranking, and needn't be complete.
"""

from __future__ import annotations

PYPI = tuple("""
boto3 botocore urllib3 requests setuptools certifi charset-normalizer idna typing-extensions python-dateutil
s3transfer packaging six numpy pyyaml fsspec s3fs pip cryptography cffi pycparser pandas importlib-metadata
protobuf pyasn1 pyasn1-modules rsa zipp attrs cattrs jmespath click platformdirs wheel pydantic pydantic-core
jinja2 markupsafe pytz filelock colorama awscli toml tomli tomlkit virtualenv pyjwt cachetools google-auth
google-api-core googleapis-common-protos pluggy pytest wrapt jsonschema psutil iniconfig annotated-types
sqlalchemy aiohttp multidict yarl frozenlist aiosignal async-timeout tzdata pyarrow docutils
requests-oauthlib oauthlib greenlet exceptiongroup scipy pygments soupsieve beautifulsoup4 decorator lxml
pyparsing werkzeug flask itsdangerous h11 httpx httpcore anyio sniffio grpcio grpcio-status distlib
more-itertools openpyxl tqdm rich markdown-it-py regex pillow matplotlib kiwisolver fonttools scikit-learn
joblib threadpoolctl networkx sympy tabulate coverage pytest-cov pytest-mock pytest-asyncio pytest-xdist
mock black mypy pathspec isort flake8 pycodestyle pyflakes pylint astroid ruff pre-commit tox django
djangorestframework fastapi starlette uvicorn gunicorn celery redis kombu psycopg psycopg2 psycopg2-binary
pymysql paramiko bcrypt pynacl docker websocket-client websockets simplejson ujson orjson msgpack markdown
python-dotenv dnspython email-validator typer tenacity backoff xmltodict chardet arrow pendulum babel sphinx
transformers torch tokenizers huggingface-hub safetensors openai anthropic langchain tiktoken
google-cloud-storage azure-core azure-storage-blob msal opentelemetry-api opentelemetry-sdk prometheus-client
sentry-sdk selenium playwright scrapy twisted gevent tornado pymongo elasticsearch kafka-python pyopenssl
ipython jupyter notebook ipykernel seaborn plotly statsmodels xgboost lightgbm tensorflow keras opencv-python
hypothesis freezegun responses faker marshmallow pyzmq pexpect wcwidth prompt-toolkit termcolor loguru
structlog humanize boto
""".split())

NPM = tuple("""
lodash react react-dom chalk tslib commander express axios debug ms semver uuid moment async request
bluebird underscore fs-extra glob minimist mkdirp rimraf yargs inquirer colors prop-types classnames webpack
webpack-cli @babel/core @babel/preset-env @babel/runtime @babel/preset-react @babel/preset-typescript
typescript eslint prettier jest mocha chai sinon ts-node nodemon dotenv body-parser cors cookie-parser
jsonwebtoken bcrypt bcryptjs mongoose mongodb mysql mysql2 pg redis ioredis sequelize knex socket.io
socket.io-client ws node-fetch cross-fetch got superagent qs query-string rxjs zone.js vue vue-router vuex
pinia @angular/core svelte next nuxt preact jquery bootstrap tailwindcss postcss autoprefixer sass less
styled-components @emotion/react redux react-redux @reduxjs/toolkit mobx zustand immer react-router
react-router-dom @types/node @types/react @types/react-dom @types/jest vite rollup esbuild parcel
@vitejs/plugin-react vitest cypress @playwright/test puppeteer cheerio jsdom handlebars ejs pug marked
highlight.js date-fns dayjs luxon moment-timezone yaml js-yaml json5 ajv joi yup zod validator nanoid
cross-env concurrently npm-run-all husky lint-staged chokidar ora boxen winston morgan pino helmet passport
express-session multer sharp aws-sdk @aws-sdk/client-s3 firebase firebase-admin stripe graphql
@apollo/client graphql-tag typeorm prisma @prisma/client electron react-native expo three d3 chart.js
core-js regenerator-runtime source-map source-map-support acorn terser uglify-js html-webpack-plugin
css-loader style-loader babel-loader ts-loader eslint-plugin-react eslint-config-prettier
@typescript-eslint/parser @typescript-eslint/eslint-plugin tsx execa cross-spawn which open lru-cache
iconv-lite safe-buffer inherits readable-stream string-width strip-ansi ansi-styles supports-color
wrap-ansi escape-string-regexp picomatch micromatch fast-glob globby minimatch ignore
""".split())

CRATES = tuple("""
serde serde_json serde_derive serde_yaml rand rand_core rand_chacha libc cfg-if syn quote proc-macro2
unicode-ident log bitflags itoa ryu memchr lazy_static once_cell regex regex-syntax aho-corasick autocfg
byteorder base64 hashbrown indexmap either itertools smallvec parking_lot lock_api scopeguard getrandom
tokio tokio-util tokio-stream futures futures-util futures-core pin-project pin-project-lite bytes mio
socket2 http http-body hyper hyper-util h2 reqwest url idna percent-encoding tower tower-http tracing
tracing-core tracing-subscriber thiserror anyhow clap clap_derive strsim heck textwrap termcolor env_logger
humantime chrono time num-traits num-integer num-bigint num_cpus crossbeam crossbeam-utils
crossbeam-channel rayon semver toml uuid sha2 sha1 md-5 digest generic-array hex hmac ring rustls
rustls-pemfile webpki-roots openssl native-tls cc pkg-config flate2 miniz_oxide crc32fast walkdir tempfile
fastrand glob dirs home which nix winapi windows-sys async-trait async-std actix-web axum warp rocket tonic
prost diesel sqlx rusqlite redis dashmap arc-swap slab static_assertions memoffset unicode-width
unicode-segmentation encoding_rs bstr nom pest criterion proptest quickcheck mockall insta serde_with
bincode strum derive_more paste bumpalo wasm-bindgen js-sys web-sys image ndarray indicatif console
dialoguer colored crossterm ratatui zstd tar zip structopt
""".split())

BY_ECOSYSTEM = {"pypi": PYPI, "npm": NPM, "crates": CRATES}
