"""Every top-level name each of these standard-library modules has had, in
CPython 3.8 to 3.15, submodules included. Written by scripts/stdlib_names.py
from CPython's sources; don't edit it by hand.

A module is here only if its names can be listed from its source in every
one of those releases. See repo/unknown_names.py for how it's used."""

NEWEST = 15  # the last Python 3 minor release read

NAMES: dict[str, frozenset[str]] = {
    "argparse": frozenset("""
        Action ArgumentDefaultsHelpFormatter ArgumentError ArgumentParser ArgumentTypeError BooleanOptionalAction FileType
        HelpFormatter MetavarTypeHelpFormatter Namespace ONE_OR_MORE OPTIONAL PARSER REMAINDER RawDescriptionHelpFormatter
        RawTextHelpFormatter SUPPRESS ZERO_OR_MORE _ _ActionsContainer _AppendAction _AppendConstAction _ArgumentGroup
        _AttributeHolder _ColorlessTheme _CountAction _ExtendAction _HelpAction _MutuallyExclusiveGroup _StoreAction
        _StoreConstAction _StoreFalseAction _StoreTrueAction _SubParsersAction _UNRECOGNIZED_ARGS_ATTR _VersionAction __all__
        __getattr__ __version__ _colorize _colorless_theme _copy_items _deprecated_default _get_action_name _identity _os _prog_name
        _re _shutil _sys ngettext warnings
    """.split()),
    "base64": frozenset("""
        MAXBINSIZE MAXLINESIZE _85encode _A85END _A85START _B32_DECODE_DOCSTRING _B32_DECODE_MAP01_DOCSTRING _B32_ENCODE_DOCSTRING
        _NOT_SPECIFIED __all__ _a85chars _a85chars2 _b32alphabet _b32decode _b32encode _b32hexalphabet _b32rev _b32tab2 _b85alphabet
        _b85chars _b85chars2 _b85dec _bytes_from_decode_data _input_type_check _urlsafe_decode_translation
        _urlsafe_encode_translation _z85_b85_decode_diff _z85_decode_translation _z85_encode_translation _z85alphabet a85decode
        a85encode b16decode b16encode b32decode b32encode b32hexdecode b32hexencode b64decode b64encode b85decode b85encode binascii
        bytes_types decode decodebytes decodestring encode encodebytes encodestring main re standard_b64decode standard_b64encode
        struct test urlsafe_b64decode urlsafe_b64encode z85decode z85encode
    """.split()),
    "collections.abc": frozenset("""
        ABCMeta AsyncGenerator AsyncIterable AsyncIterator Awaitable Buffer ByteString Callable Collection Container Coroutine
        EllipsisType FunctionType Generator GenericAlias Hashable ItemsView Iterable Iterator KeysView Mapping MappingView
        MutableMapping MutableSequence MutableSet Reversible Sequence Set Sized ValuesView _CallableGenericAlias
        _DeprecateByteStringMeta __all__ __name__ _ag _check_methods _coro _f _is_param_expr _is_typevarlike _type_repr
        abstractmethod async_generator bytearray_iterator bytes_iterator coroutine dict_itemiterator dict_items dict_keyiterator
        dict_keys dict_valueiterator dict_values generator list_iterator list_reverseiterator longrange_iterator mappingproxy
        range_iterator set_iterator str_iterator sys tuple_iterator zip_iterator
    """.split()),
    "configparser": frozenset("""
        BasicInterpolation ConfigParser ConverterMapping DEFAULTSECT DuplicateOptionError DuplicateSectionError Error
        ExtendedInterpolation Interpolation InterpolationDepthError InterpolationError InterpolationMissingOptionError
        InterpolationSyntaxError InvalidWriteError Iterable LegacyInterpolation MAX_INTERPOLATION_DEPTH MissingSectionHeaderError
        MultilineContinuationError MutableMapping NoOptionError NoSectionError ParsingError RawConfigParser SafeConfigParser
        SectionProxy UNNAMED_SECTION UnnamedSectionDisabledError _ChainMap _CommentSpec _Line _ReadState _UNSET _UnnamedSection
        __all__ _default_dict contextlib functools io itertools os re sys types warnings
    """.split()),
    "contextlib": frozenset("""
        AbstractAsyncContextManager AbstractContextManager AsyncContextDecorator AsyncExitStack ContextDecorator ExitStack
        GenericAlias MethodType _AsyncGeneratorContextManager _BaseExitStack _GeneratorContextManager _GeneratorContextManagerBase
        _RedirectStream __all__ _collections_abc _isasyncgenfunction _iscoroutinefunction _isgeneratorfunction _lookup_special
        _sentinel abc aclosing asynccontextmanager chdir closing contextmanager deque nullcontext os redirect_stderr redirect_stdout
        suppress sys wraps
    """.split()),
    "copy": frozenset("""
        Error PyStringMap __all__ _atomic_types _copy_atomic_types _copy_builtin_containers _copy_dispatch _copy_immutable
        _deepcopy_atomic _deepcopy_dict _deepcopy_dispatch _deepcopy_frozendict _deepcopy_list _deepcopy_method _deepcopy_tuple
        _keep_alive _reconstruct copy d deepcopy dispatch_table error replace t types weakref
    """.split()),
    "csv": frozenset("""
        Dialect DictReader DictWriter Error QUOTE_ALL QUOTE_MINIMAL QUOTE_NONE QUOTE_NONNUMERIC QUOTE_NOTNULL QUOTE_STRINGS Sniffer
        StringIO _Dialect __all__ __doc__ __getattr__ __version__ complex excel excel_tab field_size_limit get_dialect list_dialects
        re reader register_dialect types unix_dialect unregister_dialect writer
    """.split()),
    "difflib": frozenset("""
        Differ GenericAlias HtmlDiff IS_CHARACTER_JUNK IS_LINE_JUNK Match SequenceMatcher __all__ _calculate_ratio _check_types
        _file_template _format_range_context _format_range_unified _keep_original_ws _legend _mdiff _namedtuple _nlargest _styles
        _table_template _test can_colorize context_diff diff_bytes get_close_matches get_theme ndiff re restore unified_diff
    """.split()),
    "fnmatch": frozenset("""
        __all__ _compile_pattern _join_translated_parts _nextgroupnum _re_escape _re_setops_sub _translate count filter filterfalse
        fnmatch fnmatchcase functools itertools os posixpath re translate
    """.split()),
    "fractions": frozenset("""
        Decimal Fraction _FLOAT_FORMAT_SPECIFICATION_MATCHER _GENERAL_FORMAT_SPECIFICATION_MATCHER _PyHASH_INF _PyHASH_MODULUS
        _RATIONAL_FORMAT __all__ _gcd _hash_algorithm _round_to_exponent _round_to_figures functools gcd math numbers operator re
        sys
    """.split()),
    "functools": frozenset("""
        FunctionType GenericAlias MappingProxyType MethodType Placeholder RLock UnionType WRAPPER_ASSIGNMENTS WRAPPER_UPDATES
        _CacheInfo _HashedSeq _NOT_FOUND _PlaceholderType __all__ _c3_merge _c3_mro _compose_mro _convert _find_impl _ge_from_gt
        _ge_from_le _ge_from_lt _gt_from_ge _gt_from_le _gt_from_lt _initial_missing _le_from_ge _le_from_gt _le_from_lt
        _lru_cache_wrapper _lt_from_ge _lt_from_gt _lt_from_le _make_key _partial_new _partial_prepare_merger _partial_repr
        _singledispatchmethod_get _unwrap_partial _unwrap_partialmethod _warn_python_reduce_kwargs cache cached_property cmp_to_key
        get_cache_token itemgetter lru_cache namedtuple partial partialmethod recursive_repr reduce singledispatch
        singledispatchmethod total_ordering update_wrapper wraps
    """.split()),
    "getpass": frozenset("""
        GetPassWarning _POSIX_CTRL_CHARS _PasswordLineEditor __all__ _check_echo_char _get_terminal_ctrl_chars _raw_input
        _readline_with_echo_char contextlib fallback_getpass getpass getuser io msvcrt os sys termios unix_getpass warnings
        win_getpass
    """.split()),
    "glob": frozenset("""
        _Globber _GlobberBase _PathGlobber _StringGlobber __all__ _compile_pattern _deprecated_function_message _dir_open_flags
        _glob0 _glob1 _glob2 _iglob _isdir _ishidden _isrecursive _iterdir _join _lexists _listdir _no_recurse_symlinks _rlistdir
        _special_parts contextlib escape fnmatch functools glob glob0 glob1 has_magic iglob itertools magic_check magic_check_bytes
        operator os re stat sys translate
    """.split()),
    "gzip": frozenset("""
        BadGzipFile FCOMMENT FEXTRA FHCRC FNAME FTEXT GzipFile READ READ_BUFFER_SIZE WRITE _COMPRESS_LEVEL_BEST _COMPRESS_LEVEL_FAST
        _COMPRESS_LEVEL_TRADEOFF _GzipReader _PaddedFile _WRITE_BUFFER_SIZE _WriteBufferStream __all__ _compression
        _create_simple_gzip_header _read_exact _read_gzip_header _read_until_null _streams builtins compress decompress io main open
        os struct sys time weakref write32u zlib
    """.split()),
    "hmac": frozenset("""
        HMAC _compute_digest_fallback _functype _get_digest_constructor _hashlib _hashopenssl _hmac _is_shake_constructor
        _openssl_md_meths _select_blocksize _warnings compare_digest digest digest_size new trans_36 trans_5C x
    """.split()),
    "html": frozenset("""
        __all__ _charref _html5 _invalid_charrefs _invalid_codepoints _re _replace_charref entities escape parser unescape
    """.split()),
    "io": frozenset("""
        BlockingIOError BufferedIOBase BufferedRWPair BufferedRandom BufferedReader BufferedWriter BytesIO DEFAULT_BUFFER_SIZE
        FileIO GenericAlias IOBase IncrementalNewlineDecoder OpenWrapper RawIOBase Reader SEEK_CUR SEEK_END SEEK_SET StringIO
        TextIOBase TextIOWrapper UnsupportedOperation Writer _WindowsConsoleIO __all__ __author__ __getattr__ _check_methods _io abc
        klass open open_code text_encoding
    """.split()),
    "ipaddress": frozenset("""
        AddressValueError IPV4LENGTH IPV6LENGTH IPv4Address IPv4Interface IPv4Network IPv6Address IPv6Interface IPv6Network
        NetmaskValueError _BaseAddress _BaseConstants _BaseNetwork _BaseV4 _BaseV6 _IPAddressBase _IPv4Constants _IPv6Constants
        __getattr__ __version__ _address_fmt_re _collapse_addresses_internal _count_righthand_zero_bits _find_address_range
        _split_optional_netmask collapse_addresses functools get_mixed_type_key ip_address ip_interface ip_network
        summarize_address_range v4_int_to_packed v6_int_to_packed
    """.split()),
    "json": frozenset("""
        JSONDecodeError JSONDecoder JSONEncoder __all__ __author__ __getattr__ __version__ _default_decoder _default_encoder codecs
        decoder detect_encoding dump dumps encoder load loads scanner tool
    """.split()),
    "keyword": frozenset("""
        __all__ iskeyword issoftkeyword kwlist softkwlist
    """.split()),
    "linecache": frozenset("""
        __all__ _bless_my_loader _getline_from_code _getlines_from_code _interactive_cache _make_key _make_lazycache_entry
        _read_from_archive _register_code _source_unavailable cache checkcache clearcache functools getline getlines lazycache os
        sys tokenize updatecache
    """.split()),
    "logging": frozenset("""
        BASIC_FORMAT BufferingFormatter CRITICAL DEBUG ERROR FATAL FileHandler Filter Filterer Formatter GenericAlias Handler INFO
        LogRecord Logger LoggerAdapter Manager NOTSET NullHandler PercentStyle PlaceHolder RootLogger StrFormatStyle StrFormatter
        StreamHandler StringTemplateStyle Template WARN WARNING _STYLES _StderrHandler __all__ __author__ __date__ __getattr__
        __status__ __version__ _acquireLock _addHandlerRef _afterFork _after_at_fork_child_reinit_locks _at_fork_reinit_lock_weakset
        _checkLevel _defaultFormatter _defaultLastResort _handlerList _handlers _is_internal_frame _levelToName _lock
        _logRecordFactory _loggerClass _nameToLevel _prepareFork _register_at_fork_reinit_lock _releaseLock _removeHandlerRef
        _showwarning _srcfile _startTime _str_formatter _warnings_showwarning addLevelName atexit basicConfig captureWarnings
        collections config critical currentframe debug disable error exception fatal getHandlerByName getHandlerNames getLevelName
        getLevelNamesMapping getLogRecordFactory getLogger getLoggerClass handlers info io lastResort log logAsyncioTasks
        logMultiprocessing logProcesses logThreads makeLogRecord os raiseExceptions re root setLogRecordFactory setLoggerClass
        shutdown sys threading time traceback warn warning warnings weakref
    """.split()),
    "logging.config": frozenset("""
        BaseConfigurator ConvertingDict ConvertingList ConvertingMixin ConvertingTuple DEFAULT_LOGGING_CONFIG_PORT DictConfigurator
        IDENTIFIER RESET_ERROR StreamRequestHandler ThreadingTCPServer _clearExistingHandlers _create_formatters
        _handle_existing_loggers _install_handlers _install_loggers _is_queue_like_object _listener _resolve _strip_spaces
        dictConfig dictConfigClass errno fileConfig functools io listen logging os queue re socket stopListening struct sys
        threading traceback valid_ident
    """.split()),
    "logging.handlers": frozenset("""
        BaseRotatingHandler BufferingHandler DEFAULT_HTTP_LOGGING_PORT DEFAULT_SOAP_LOGGING_PORT DEFAULT_TCP_LOGGING_PORT
        DEFAULT_UDP_LOGGING_PORT DatagramHandler HTTPHandler MemoryHandler NTEventLogHandler QueueHandler QueueListener
        RotatingFileHandler SMTPHandler ST_DEV ST_INO ST_MTIME SYSLOG_TCP_PORT SYSLOG_UDP_PORT SocketHandler SysLogHandler
        TimedRotatingFileHandler WatchedFileHandler _MIDNIGHT copy io logging os pickle queue re socket struct threading time
    """.split()),
    "mimetypes": frozenset("""
        MimeTypes __all__ _common_types_default _db _default_mime_types _encodings_map_default _main
        _mimetypes_read_windows_registry _parse_args _suffix_map_default _types_map_default _winreg add_type common_types
        encodings_map guess_all_extensions guess_extension guess_file_type guess_type init inited knownfiles os posixpath
        read_mime_types result results suffix_map sys types_map urllib
    """.split()),
    "numbers": frozenset("""
        ABCMeta Complex Integral Number Rational Real __all__ abstractmethod
    """.split()),
    "os.path": frozenset("""
        ALLOW_MISSING ALL_BUT_LAST _LCMAP_LOWERCASE _LCMapStringEx _LOCALE_NAME_INVARIANT __all__ _abspath_fallback _check_arg_types
        _commonprefix _findfirstfile _get_bothseps _get_sep _getfinalpathname _getfinalpathname_nonstrict _getfullpathname
        _getvolumepathname _isreservedname _joinrealpath _nt_readlink _path_isdevdrive _path_normpath _readlink_deep _reserved_chars
        _reserved_names _splitext _varpattern _varprog _varprogb _varsub _varsubb abspath altsep basename c commonpath commonprefix
        curdir defpath devnull dirname errno exists expanduser expandvars extsep genericpath getatime getctime getmtime getsize i
        isabs isdevdrive isdir isfile isjunction islink ismount isreserved join lexists normcase normpath os pardir pathsep realpath
        relpath samefile sameopenfile samestat sep split splitdrive splitext splitroot stat supports_unicode_filenames sys
    """.split()),
    "platform": frozenset("""
        AndroidVer IOSVersionInfo _Processor _SIMPLE_SUBSTITUTIONS _WIN32_CLIENT_RELEASES _WIN32_SERVER_RELEASES __copyright__
        __getattr__ __version__ _comparable_version _component_re _default_architecture _follow_symlinks _get_machine_win32
        _ironpython26_sys_version_parser _ironpython_sys_version_parser _java_getprop _libc_search _mac_ver_xml _main _node
        _norm_version _os_release_cache _os_release_candidates _os_release_line _os_release_unescape _parse_args _parse_os_release
        _platform _platform_cache _pypy_sys_version_parser _sys_version _sys_version_cache _sys_version_parser _syscmd_file
        _syscmd_uname _syscmd_ver _uname_cache _unknown_as_blank _ver_output _ver_stages _win32_ver _wmi _wmi_query aliased
        android_ver architecture collections freedesktop_os_release functools invalidate_caches ios_ver itertools java_ver libc_ver
        mac_ver machine node os platform processor python_branch python_build python_compiler python_implementation python_revision
        python_version python_version_tuple re release subprocess sys system system_alias terse uname uname_result version
        win32_edition win32_is_iot win32_ver
    """.split()),
    "queue": frozenset("""
        Empty Full LifoQueue PriorityQueue Queue ShutDown SimpleQueue _PySimpleQueue __all__ deque heappop heappush threading time
        types
    """.split()),
    "random": frozenset("""
        BPF LOG4 NV_MAGICCONST RECIP_BPF Random SG_MAGICCONST SystemRandom TWOPI _ONE _Sequence _Set __all__ _accumulate _acos
        _bisect _ceil _cos _e _exp _fabs _floor _index _inst _isfinite _lgamma _log _log2 _os _parse_args _pi _random _repeat
        _sha512 _sin _sqrt _test _test_generator _urandom _warn betavariate binomialvariate choice choices expovariate gammavariate
        gauss getrandbits getstate lognormvariate main normalvariate paretovariate randbytes randint random randrange sample seed
        setstate shuffle triangular uniform vonmisesvariate weibullvariate
    """.split()),
    "secrets": frozenset("""
        DEFAULT_ENTROPY SystemRandom __all__ _sysrand base64 binascii choice compare_digest os randbelow randbits token_bytes
        token_hex token_urlsafe
    """.split()),
    "shlex": frozenset("""
        StringIO __all__ _find_unsafe _print_tokens deque f fn join os quote re shlex split sys
    """.split()),
    "shutil": frozenset("""
        COPY_BUFSIZE Error ExecError ReadError RegistryError SameFileError SpecialFileError _ARCHIVE_FORMATS _BZ2_SUPPORTED
        _GiveupOnFastCopy _HAS_FCOPYFILE _LZMA_SUPPORTED _UNPACK_FORMATS _USE_CP_COPY_FILE_RANGE _USE_CP_SENDFILE _WINDOWS
        _WIN_DEFAULT_PATHEXT _ZLIB_SUPPORTED _ZSTD_SUPPORTED __all__ __getattr__ _access_check _basename _check_unpack_options
        _copyfileobj_readinto _copytree _copyxattr _destinsrc _determine_linux_fastcopy_blocksize _ensure_directory
        _fastcopy_copy_file_range _fastcopy_fcopyfile _fastcopy_sendfile _find_unpack_format _get_gid _get_uid _is_immutable _islink
        _make_tarball _make_zipfile _ntuple_diskusage _rmtree_impl _rmtree_isdir _rmtree_islink _rmtree_safe_fd _rmtree_safe_fd_step
        _rmtree_unsafe _samefile _stat _unpack_tarfile _unpack_zipfile _use_fd_functions _win_path_needs_curdir _winapi bz2 chown
        collections copy copy2 copyfile copyfileobj copymode copystat copytree disk_usage errno fnmatch get_archive_formats
        get_terminal_size get_unpack_formats getgrnam getpwnam ignore_patterns lzma make_archive move nt os posix
        register_archive_format register_unpack_format rmtree stat sys unpack_archive unregister_archive_format
        unregister_unpack_format warnings which zlib zstd
    """.split()),
    "statistics": frozenset("""
        Counter Decimal Fraction G1 G2 LinearRegression NormalDist S StatisticsError X Y _SQRT2 _SQRT2PI __all__ _coerce _convert
        _decimal_sqrt_of_frac _exact_ratio _fail_neg _find_lteq _find_rteq _float_sqrt_of_frac _integer_sqrt_of_frac_rto _isfinite
        _kernel_invcdfs _kernel_specs _mean_stdev _newton_raphson _normal_dist_inv_cdf _quartic_invcdf _quartic_invcdf_estimate
        _random _rank _sqrt_bit_width _sqrtprod _ss _sum _triweight_invcdf _triweight_invcdf_estimate acos add asin assert_close
        atan bisect_left bisect_right compress const correlation cos cosh cosine_kernel count covariance defaultdict doctest erf
        erfc exp fabs fmean fsum func g1 g2 geometric_mean groupby harmonic_mean hypot isclose isfinite isinf itemgetter kde
        kde_random linear_regression log logistic_kernel math mean median median_grouped median_high median_low mode mul multimode n
        namedtuple normal_kernel numbers parabolic_kernel pi pstdev pvariance quantiles quartic_kernel random rectangular_kernel
        reduce register repeat s sigmoid_kernel sin sqrt stdev sub sumprod sys tan tau triangular_kernel triweight_kernel truediv
        variance x y
    """.split()),
    "string": frozenset("""
        Formatter Template _ChainMap _TemplateMetaclass _TemplatePattern __all__ _re _sentinel_dict _string ascii_letters
        ascii_lowercase ascii_uppercase capwords digits hexdigits octdigits printable punctuation templatelib whitespace
    """.split()),
    "subprocess": frozenset("""
        ABOVE_NORMAL_PRIORITY_CLASS BELOW_NORMAL_PRIORITY_CLASS CREATE_BREAKAWAY_FROM_JOB CREATE_DEFAULT_ERROR_MODE
        CREATE_NEW_CONSOLE CREATE_NEW_PROCESS_GROUP CREATE_NO_WINDOW CalledProcessError CompletedProcess DETACHED_PROCESS DEVNULL
        HIGH_PRIORITY_CLASS Handle IDLE_PRIORITY_CLASS NORMAL_PRIORITY_CLASS PIPE Popen REALTIME_PRIORITY_CLASS
        STARTF_FORCEOFFFEEDBACK STARTF_FORCEONFEEDBACK STARTF_USESHOWWINDOW STARTF_USESTDHANDLES STARTUPINFO STDOUT STD_ERROR_HANDLE
        STD_INPUT_HANDLE STD_OUTPUT_HANDLE SW_HIDE SubprocessError TimeoutExpired _CAN_USE_KQUEUE _CAN_USE_PIDFD_OPEN
        _HAVE_POSIX_SPAWN_CLOSEFROM _MAXIMUM_WAIT_TIMEOUT _PIPE_BUF _PopenSelector _USE_POSIX_SPAWN _USE_VFORK _WIFSTOPPED _WNOHANG
        _WSTOPSIG __all__ _active _args_from_interpreter_flags _can_fork_exec _can_use_kqueue _can_use_pidfd_open _cleanup
        _communicate_io_posix _deadline_remaining _del_safe _flush_stdin _fork_exec _make_input_view _mswindows
        _optim_args_from_interpreter_flags _posixsubprocess _text_encoding _time _translate_newlines _use_posix_spawn _waitpid
        _waitstatus_to_exitcode _winapi builtins call check_call check_output contextlib errno fcntl getoutput getstatusoutput grp
        io list2cmdline locale msvcrt os pwd run select selectors signal sys threading time types warnings
    """.split()),
    "tarfile": frozenset("""
        AREGTYPE AbsoluteLinkError AbsolutePathError BLKTYPE BLOCKSIZE CHRTYPE CONTTYPE CompressionError DEFAULT_FORMAT DIRTYPE
        ENCODING EOFHeaderError EmptyHeaderError ExFileObject ExtractError FIFOTYPE FilterError GNUTYPE_LONGLINK GNUTYPE_LONGNAME
        GNUTYPE_SPARSE GNU_FORMAT GNU_MAGIC GNU_TYPES HeaderError InvalidHeaderError LENGTH_LINK LENGTH_NAME LENGTH_PREFIX LNKTYPE
        LinkFallbackError LinkOutsideDestinationError NUL OutsideDestinationError PAX_FIELDS PAX_FORMAT PAX_NAME_FIELDS
        PAX_NUMBER_FIELDS POSIX_MAGIC RECORDSIZE REGTYPE REGULAR_TYPES ReadError SOLARIS_XHDTYPE SUPPORTED_TYPES SYMTYPE
        SpecialFileError StreamError SubsequentHeaderError TarError TarFile TarInfo TruncatedHeaderError USTAR_FORMAT XGLTYPE
        XHDTYPE _EXTHEADER_READ_CHUNK _FILTER_ERRORS _FileInFile _KEEP _LowLevelFile _NAMED_FILTERS _Stream _StreamProxy __all__
        __author__ __credits__ __getattr__ _get_filtered_attrs _header_length_prefix_re _safe_print _safe_read bltn_open
        calc_chksums copy copyfileobj data_filter fully_trusted_filter grp io is_tarfile itn main nti nts open os pwd re shutil stat
        stn struct symlink_exception sys tar_filter time version warnings
    """.split()),
    "tempfile": frozenset("""
        NamedTemporaryFile SpooledTemporaryFile TMP_MAX TemporaryDirectory TemporaryFile _O_TMPFILE_WORKS _Random
        _RandomNameSequence _TemporaryFileCloser _TemporaryFileWrapper __all__ _allocate_lock _bin_openflags _candidate_tempdir_list
        _dont_follow_symlinks _errno _exists _functools _get_candidate_names _get_default_tempdir _gettempdir _infer_return_type _io
        _mkstemp_inner _name_sequence _nofollow_mode _once_lock _os _resetflags _resetperms _resetperms_at _resetperms_fd
        _rmtree_use_dir_fd _sanitize_params _shutil _stat _sys _text_openflags _thread _types _warnings _weakref gettempdir
        gettempdirb gettempprefix gettempprefixb mkdtemp mkstemp mktemp tempdir template
    """.split()),
    "textwrap": frozenset("""
        TextWrapper __all__ _leading_whitespace_re _whitespace _whitespace_only_re dedent fill indent re shorten wrap
    """.split()),
    "threading": frozenset("""
        Barrier BoundedSemaphore BrokenBarrierError Condition Event ExceptHookArgs Lock RLock Semaphore TIMEOUT_MAX Thread
        ThreadError Timer WeakSet _CRLock _DeleteDummyThreadOnDel _DummyThread _ExceptHookArgs _HAVE_THREAD_NATIVE_ID _LockType
        _MainThread _PyRLock _RLock _SHUTTING_DOWN _ThreadHandle __all__ __excepthook__ _active _active_limbo_lock _after_fork
        _allocate_lock _concurrent_tee _contextvars _count _counter _daemon_threads_allowed _dangling _deque _enumerate
        _get_main_thread_ident _is_main_interpreter _islice _limbo _main_thread _maintain_shutdown_locks _make_invoke_excepthook
        _make_thread_handle _newname _os _print_exception _profile_hook _register_atexit _set_name _set_sentinel _shutdown
        _shutdown_locks _shutdown_locks_lock _start_joinable_thread _start_new_thread _sys _thread _thread_local_info
        _thread_shutdown _threading_atexits _time _trace_hook activeCount active_count concurrent_tee currentThread current_thread
        enumerate excepthook functools get_ident get_native_id getprofile gettrace local main_thread namedtuple serialize_iterator
        setprofile setprofile_all_threads settrace settrace_all_threads stack_size synchronized_iterator warnings
    """.split()),
    "tomllib": frozenset("""
        TOMLDecodeError __all__ _parser _re _types load loads mypy
    """.split()),
    "unittest.mock": frozenset("""
        ANY AsyncMagicMixin AsyncMock AsyncMockMixin Base CallableMixin CodeType DEFAULT FILTER_DIR Format FunctionTypes
        InvalidSpecError LazyImportType MagicMixin MagicMock MagicProxy MethodType Mock ModuleType NonCallableMagicMock
        NonCallableMock PropertyMock RLock ThreadingMixin ThreadingMock _ANY _ATTRIB_DENY_LIST _AnyComparer _AsyncIterator
        _CODE_ATTRS _CODE_SIG _Call _CallList _MOCK_SIG _MockIter _Sentinel _SentinelObject _SpecState __all__ __version__
        _all_magics _all_sync_magics _allowed_names _async_magics _async_method_magics _builtins _calculate_return_value _callable
        _check_and_set_parent _check_signature _check_spec_arg_typos _clear_dict _copy_func_details _delegating_property _deleted
        _dot_lookup _extract_mock _format_call_signature _get_async_iter _get_eq _get_iter _get_method _get_ne _get_signature_object
        _get_target _importer _instance_callable _is_async_func _is_async_obj _is_exception _is_instance_mock _is_list _is_magic
        _magics _missing _must_skip _non_defaults _patch _patch_dict _patch_multiple _patch_object _patch_stopall _return_values
        _safe_super _set_async_signature _set_return_value _set_signature _setup_async_mock _setup_func _side_effect_methods
        _sync_async_magics _timeout_unset _to_stream _try_iter _unsupported_magics asyncio builtins call contextlib create_autospec
        fields file_spec inplace inspect io is_dataclass iscoroutinefunction magic_methods method mock_open n name numerics
        open_spec partial patch pkgutil pprint right safe_repr seal sentinel sys threading wraps
    """.split()),
    "urllib.error": frozenset("""
        ContentTooShortError HTTPError URLError __all__ io urllib
    """.split()),
    "urllib.parse": frozenset("""
        DefragResult DefragResultBytes MAX_CACHE_SIZE ParseResult ParseResultBytes Quoter ResultBase SplitResult SplitResultBytes
        _ALWAYS_SAFE _ALWAYS_SAFE_BYTES _DefragResultBase _MISSING_AS_NONE_DEFAULT _NetlocResultMixinBase _NetlocResultMixinBytes
        _NetlocResultMixinStr _ParseResultBase _Quoter _ResultBase _ResultMixinBytes _ResultMixinStr _SplitResultBase
        _UNSAFE_URL_BYTES_TO_REMOVE _UNSPECIFIED _WHATWG_C0_CONTROL_OR_SPACE __all__ __getattr__ _asciire _byte_quoter_factory
        _check_bracketed_host _check_bracketed_netloc _checknetloc _checknetloc_nfkc _coerce_args _decode_args _encode_result
        _fix_result_transcoding _generate_unquoted_parts _hexdig _hextobyte _hostprog _implicit_encoding _implicit_errors _noop
        _parse_cache _parse_hostinfo _portprog _remove_unsafe_bytes_from_url _safe_quoters _splitattr _splithost _splitnetloc
        _splitnport _splitparams _splitpasswd _splitport _splitquery _splittag _splittype _splituser _splitvalue _to_bytes _typeprog
        _unquote_impl _urlparse _urlsplit _urlunsplit _validate_port clear_cache collections functools ipaddress math namedtuple
        non_hierarchical parse_qs parse_qsl quote quote_from_bytes quote_plus re scheme_chars splitattr splithost splitnport
        splitpasswd splitport splitquery splittag splittype splituser splitvalue sys to_bytes types unquote unquote_plus
        unquote_to_bytes unwrap urldefrag urlencode urljoin urlparse urlsplit urlunparse urlunsplit uses_fragment uses_netloc
        uses_params uses_query uses_relative warnings
    """.split()),
    "urllib.request": frozenset("""
        AbstractBasicAuthHandler AbstractDigestAuthHandler AbstractHTTPHandler BaseHandler CacheFTPHandler ContentTooShortError
        DataHandler FTPHandler FancyURLopener FileHandler HTTPBasicAuthHandler HTTPCookieProcessor HTTPDefaultErrorHandler
        HTTPDigestAuthHandler HTTPError HTTPErrorProcessor HTTPHandler HTTPPasswordMgr HTTPPasswordMgrWithDefaultRealm
        HTTPPasswordMgrWithPriorAuth HTTPRedirectHandler HTTPSHandler MAXFTPCACHE OpenerDirector ProxyBasicAuthHandler
        ProxyDigestAuthHandler ProxyHandler Request URLError URLopener UnknownHandler _UNSAFE_URL_BYTES_TO_REMOVE
        _WHATWG_C0_CONTROL_OR_SPACE __all__ __version__ _cut_port_re _ftperrors _get_proxies _get_proxy_settings _have_ssl
        _is_local_authority _localhost _noheaders _opener _parse_proxy _proxy_bypass_macosx_sysconf _proxy_bypass_winreg_override
        _randombytes _safe_gethostbyname _splitattr _splithost _splitnetloc _splitpasswd _splitport _splitquery _splittag _splittype
        _splituser _splitvalue _thishost _to_bytes _url_tempfiles addclosehook addinfourl base64 bisect build_opener contextlib
        email ftpcache ftperrors ftpwrapper getproxies getproxies_environment getproxies_macosx_sysconf getproxies_registry hashlib
        http install_opener io localhost noheaders os parse_http_list parse_keqv_list pathname2url posixpath proxy_bypass
        proxy_bypass_environment proxy_bypass_macosx_sysconf proxy_bypass_registry quote re request_host socket ssl string sys
        tempfile thishost time unquote unquote_to_bytes unwrap url2pathname urlcleanup urljoin urlopen urlparse urlretrieve urlsplit
        urlunparse warnings
    """.split()),
    "uuid": frozenset("""
        Enum MAX NAMESPACE_DNS NAMESPACE_OID NAMESPACE_URL NAMESPACE_X500 NIL RESERVED_FUTURE RESERVED_MICROSOFT RESERVED_NCS
        RFC_4122 SafeUUID UUID _AIX _GETTERS _LINUX _MAC_DELIM _MAC_OMITS_LEADING_ZEROES _OS_GETTERS _RFC_4122_CLEARFLAGS_MASK
        _RFC_4122_VERSION_1_FLAGS _RFC_4122_VERSION_3_FLAGS _RFC_4122_VERSION_4_FLAGS _RFC_4122_VERSION_5_FLAGS
        _RFC_4122_VERSION_6_FLAGS _RFC_4122_VERSION_7_FLAGS _RFC_4122_VERSION_8_FLAGS _UINT_128_MAX _UuidCreate __author__
        _arp_getnode _find_mac _find_mac_near_keyword _find_mac_under_heading _generate_time_safe _get_command_stdout
        _has_stable_extractable_node _has_uuid_generate_time_safe _ifconfig_getnode _ip_getnode _ipconfig_getnode _is_universal
        _lanscan_getnode _last_counter_v7 _last_timestamp _last_timestamp_v6 _last_timestamp_v7 _load_system_functions
        _netbios_getnode _netstat_getnode _node _parse_mac _platform_system _popen _random_getnode _simple_enum _unix_getnode _uuid
        _uuid7_get_counter_and_tail _windll_getnode bytes_ getnode int_ main os platform sys time uuid1 uuid3 uuid4 uuid5 uuid6
        uuid7 uuid8
    """.split()),
    "weakref": frozenset("""
        CallableProxyType KeyedRef ProxyType ProxyTypes ReferenceType WeakKeyDictionary WeakMethod WeakSet WeakValueDictionary
        _IterationGuard __all__ _collections_abc _remove_dead_weakref finalize getweakrefcount getweakrefs itertools proxy ref sys
    """.split()),
    "zipfile": frozenset("""
        BZIP2_VERSION BadZipFile BadZipfile CompleteDirs DEFAULT_VERSION FastLookup LZMACompressor LZMADecompressor LZMA_VERSION
        LargeZipFile MAX_EXTRACT_VERSION Path PyZipFile ZIP64_LIMIT ZIP64_VERSION ZIP_BZIP2 ZIP_DEFLATED ZIP_FILECOUNT_LIMIT
        ZIP_LZMA ZIP_MAX_COMMENT ZIP_STORED ZIP_ZSTANDARD ZSTANDARD_VERSION ZipExtFile ZipFile ZipInfo _CD64_CREATE_VERSION
        _CD64_DIRECTORY_RECSIZE _CD64_DIRECTORY_SIZE _CD64_DISK_NUMBER _CD64_DISK_NUMBER_START _CD64_EXTRACT_VERSION
        _CD64_NUMBER_ENTRIES_THIS_DISK _CD64_NUMBER_ENTRIES_TOTAL _CD64_OFFSET_START_CENTDIR _CD64_SIGNATURE _CD_COMMENT_LENGTH
        _CD_COMPRESSED_SIZE _CD_COMPRESS_TYPE _CD_CRC _CD_CREATE_SYSTEM _CD_CREATE_VERSION _CD_DATE _CD_DISK_NUMBER_START
        _CD_EXTERNAL_FILE_ATTRIBUTES _CD_EXTRACT_SYSTEM _CD_EXTRACT_VERSION _CD_EXTRA_FIELD_LENGTH _CD_FILENAME_LENGTH _CD_FLAG_BITS
        _CD_INTERNAL_FILE_ATTRIBUTES _CD_LOCAL_HEADER_OFFSET _CD_SIGNATURE _CD_TIME _CD_UNCOMPRESSED_SIZE _DD_SIGNATURE _ECD_COMMENT
        _ECD_COMMENT_SIZE _ECD_DISK_NUMBER _ECD_DISK_START _ECD_ENTRIES_THIS_DISK _ECD_ENTRIES_TOTAL _ECD_LOCATION _ECD_OFFSET
        _ECD_SIGNATURE _ECD_SIZE _EXTRA_FIELD_STRUCT _EndRecData _EndRecData64 _Extra _FH_COMPRESSED_SIZE _FH_COMPRESSION_METHOD
        _FH_CRC _FH_EXTRACT_SYSTEM _FH_EXTRACT_VERSION _FH_EXTRA_FIELD_LENGTH _FH_FILENAME_LENGTH _FH_GENERAL_PURPOSE_FLAG_BITS
        _FH_LAST_MOD_DATE _FH_LAST_MOD_TIME _FH_SIGNATURE _FH_UNCOMPRESSED_SIZE _MASK_COMPRESSED_PATCH _MASK_COMPRESS_OPTION_1
        _MASK_ENCRYPTED _MASK_STRONG_ENCRYPTION _MASK_USE_DATA_DESCRIPTOR _MASK_UTF_FILENAME _SharedFile _Tellable _ZipDecrypter
        _ZipWriteFile __all__ _ancestry _check_compression _check_zipfile _crctable _dedupe _difference _extract_text_encoding
        _gen_crc _get_compressor _get_decompressor _handle_prepended_data _parents _path _sanitize_filename _strip_extra binascii
        bz2 compressor_names contextlib crc32 error functools importlib io is_zipfile itertools lzma main os pathlib posixpath re
        shutil sizeCentralDir sizeEndCentDir sizeEndCentDir64 sizeEndCentDir64Locator sizeFileHeader stat stringCentralDir
        stringEndArchive stringEndArchive64 stringEndArchive64Locator stringFileHeader struct structCentralDir structEndArchive
        structEndArchive64 structEndArchive64Locator structFileHeader sys threading time zlib zstd
    """.split()),
}
