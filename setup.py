import setuptools

setuptools.setup(
    name='messy_fediverse',
    version='0.0.1',
    description='messy_fediverse: adds fediverse compatibility layer to django-based site',
    classifiers=[
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: Implementation :: CPython',
        'License :: OSI Approved :: MIT License',
        'Operating System :: Unix',
        'Operating System :: POSIX',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content :: Content Management System',
        'Framework :: Django',
    ],
    author='Pyroman',
    author_email='pyroman-github@ofthe.top',
    python_requires='>=3.6',
    install_requires=[
        #'examplelib==1.0.1',
        #'examplellib2>=2.2',
        'django',
        'requests',
        'pyOpenSSL',
    ],
    zip_safe=False,
    include_package_data=True,
    package_dir={"": "src"},   # tell distutils packages are under src
    #packages=setuptools.find_packages("src"),  # include all packages under src
    packages=[
        'messy_fediverse',
        'messy_fediverse.static.messy.fediverse',
        'messy_fediverse.templates',
        'messy_fediverse.templates.messy.fediverse',
    ],
    package_data={
        '': ['*.conf', '*.md', '*.txt', '*.html', '*.js', '*.css', '*.json', '*.jsonld']
    },
)
