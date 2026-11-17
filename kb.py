"""知识库唯一命令入口。运行 --help 查看命令与选项。"""
def main(argv=None):
    import os
    os.environ.setdefault('OPENBLAS_NUM_THREADS', os.environ.get('KB_BLAS_THREADS', '1'))
    os.environ.setdefault('OMP_NUM_THREADS', os.environ.get('KB_BLAS_THREADS', '1'))
    from kbapp.workflows.cli import main as dispatch
    return dispatch(argv)


if __name__ == '__main__':
    raise SystemExit(main())
