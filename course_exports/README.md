# Put the course export here

The `.tar.gz` you export from edX Studio (**Tools → Export → Export Course Content**)
goes in this folder. It is named `course.<something>.tar.gz`.

Then run, from the project folder:

```
python start_run.py
```

That finds the export, checks it really is a course archive, records which one it read,
and prints the commands to run next.

## One at a time

Keep **one** export here. With two and no `--tar`, every tool refuses and lists both
rather than choosing — picking the newer file by date is not safe, because OneDrive
rewrites modification times on sync, so "newest file" does not mean "newest export".

To keep an old one, move it out of this folder, or name the one you want:

```
python start_run.py --tar "course_exports/course.hp_m6v88.tar.gz"
```

## Not edXUpdater's archive

`edXUpdater` builds an archive named exactly `course.tar.gz` and uploads it **to** edX to
update the course home page. This project reads an export pulled **from** edX Studio —
the other direction, a different tree. Its archive is a valid OLX course, so dropped in
here it would be used without complaint; `start_run.py` warns when it sees that filename,
but keeping it out of this folder is the real safeguard.

## Nothing here is committed

`*.tar.gz` is in `.gitignore`, so exports stay out of git. This README is the only
tracked file in the folder.
