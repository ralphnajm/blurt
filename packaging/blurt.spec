Name:           blurt
Version:        %{blurt_version}
Release:        1%{?dist}
Summary:        Talk anywhere you can type
License:        LicenseRef-Proprietary
URL:            https://github.com/ralphnajm/blurt
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-poetry-core
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils

Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita
Requires:       libsecret
Requires:       portaudio
Requires:       wl-clipboard
Requires:       libnotify
Requires:       libcanberra-gtk3
Requires:       xdg-desktop-portal
Requires:       gnome-shell

%description
Press a shortcut, say it, and Blurt types clean, filler-free text into
whatever app you are in. Speech is transcribed with OpenAI, streaming while
you speak or after you stop, then cleaned of fillers and false starts and
typed into the focused window. A settings window keeps the API key in the
GNOME keyring and tracks spend against a monthly budget, and a top bar menu
starts and stops dictation.

%prep
%autosetup

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -L blurt
packaging/install-data.sh %{buildroot}
desktop-file-validate %{buildroot}%{_datadir}/applications/dev.blurt.Blurt.desktop

%post
%systemd_user_post blurt.service

%preun
%systemd_user_preun blurt.service

%postun
%if %{defined systemd_user_postun_with_restart}
%systemd_user_postun_with_restart blurt.service
%else
%systemd_user_postun blurt.service
%endif

%files -f %{pyproject_files}
%{_bindir}/blurt
%{_userunitdir}/blurt.service
%{_datadir}/applications/dev.blurt.Blurt.desktop
%{_datadir}/icons/hicolor/*/apps/dev.blurt.Blurt.png
%{_datadir}/gnome-shell/extensions/blurt@dev.blurt/

%changelog
%(cat %{blurt_changelog})
