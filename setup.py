from setuptools import setup, find_packages


setup(name='pl_etcd',
      version='0.0.1',
      platforms='any',
      description='Ansible etcdctl plugin.',
      author='Presslabs',
      author_email='engineering@presslabs.com',
      url='http://github.com/Presslabs/etcd_membership_plugin/',
      packages=find_packages(),
      zip_safe=False,
      include_package_data=True,
      classifiers=[
          'Programming Language :: Python :: 2.7',
      ])
