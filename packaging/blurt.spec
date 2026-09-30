%global debug_package %{nil}
%global __os_install_post %{nil}
%global _build_id_links none
%global __requires_exclude_from ^%{_prefix}/lib/blurt/.*$
%global __provides_exclude_from ^%{_prefix}/lib/blurt/.*$

Name:           blurt
Version:        %{blurt_version}
Release:        1%{?dist}
Summary:        Talk anywhere you can type
License:        LicenseRef-Proprietary
URL:            https://github.com/ralphnajm/blurt
Source0:        %{name}-%{version}.tar.gz
ExclusiveArch:  x86_64

BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils

Requires:       python(abi) = %{python3_version}
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
you speak or after you stop, or locally with Whisper. Blurt removes fillers
and false starts, then types the result into the focused window. A settings
window keeps the API key in the GNOME keyring and tracks spend against a
monthly budget, and a top bar menu starts and stops dictation.

%prep
%autosetup

%build

%install
PYTHON=%{python3} packaging/stage.sh %{buildroot}
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

%files
%{_bindir}/blurt
%{_prefix}/lib/blurt/
%{_userunitdir}/blurt.service
%{_datadir}/applications/dev.blurt.Blurt.desktop
%{_datadir}/icons/hicolor/*/apps/dev.blurt.Blurt.png
%{_datadir}/gnome-shell/extensions/blurt@dev.blurt/

%changelog
%(cat %{blurt_changelog})
