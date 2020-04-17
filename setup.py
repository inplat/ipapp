import re
from setuptools import setup, find_packages


with open('ipapp/__init__.py') as ver_file:
    ver = re.compile(r".*__version__ = '(.*?)'", re.S).match(ver_file.read())
    if ver is not None:
        version = ver.group(1)
    else:
        version = '0.0.0'

with open('requirements.txt') as requirements_file:
    requirements = requirements_file.read()
with open('requirements_postgres.txt') as requirements_file:
    requirements_postgres = requirements_file.read()
with open('requirements_oracle.txt') as requirements_file:
    requirements_oracle = requirements_file.read()
with open('requirements_rabbitmq.txt') as requirements_file:
    requirements_rabbit = requirements_file.read()
with open('requirements_iprpc.txt') as requirements_file:
    requirements_iprpc = requirements_file.read()
with open('requirements_testing.txt') as requirements_file:
    requirements_testing = requirements_file.read()
with open('requirements_s3.txt') as requirements_file:
    requirements_s3 = requirements_file.read()
with open('requirements_sftp.txt') as requirements_file:
    requirements_sftp = requirements_file.read()


setup(
    name='ipapp',
    version=str(version),
    description='InPlat application framework',
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Intended Audience :: Developers',
        'Intended Audience :: Information Technology',
        'Intended Audience :: System Administrators',
        'Operating System :: Unix',
        'Operating System :: POSIX :: Linux',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Internet',
    ],
    author='InPlat',
    url='https://gitlab.app.ipl/inplat/ipapp',
    packages=find_packages('.', exclude=['tests', 'examples']),
    python_requires='>=3.7',
    install_requires=requirements.split('\n'),
    extras_require={
        'postgres': requirements_postgres.split('\n'),
        'oracle': requirements_oracle.split('\n'),
        'rabbitmq': requirements_rabbit.split('\n'),
        'iprpc': requirements_iprpc.split('\n'),
        'testing': requirements_testing.split('\n'),
        's3': requirements_s3.split('\n'),
        'sftp': requirements_sftp.split('\n'),
    },
    entry_points={'pytest11': ['pytest_qase = ipapp.pytest.qase.plugin']},
)
