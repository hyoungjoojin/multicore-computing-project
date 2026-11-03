#!/usr/bin/env bash
set -euo pipefail

THIRD_PARTY="$(cd "$(dirname "$0")/.." && pwd)/third_party"
JOBS="$(nproc)"

checkout() {
    local dir=$1 repo=$2 rev=$3
    [[ -d $dir ]] && return
    echo "cloning $repo @ $rev"
    git init -q "$dir"
    git -C "$dir" remote add origin "$repo"
    git -C "$dir" fetch -q --depth 1 origin "$rev"
    git -C "$dir" checkout -q FETCH_HEAD
}

build() {
    local dir=$1 binary=$2
    shift 2
    [[ -x $dir/$binary ]] && return
    echo "building $dir (log: $dir/build.log)"
    if ! (cd "$dir" && "$@") >"$dir/build.log" 2>&1; then
        tail -n 20 "$dir/build.log" >&2
        echo "building $dir failed (log: $dir/build.log)" >&2
        return 1
    fi
}

fetch() {
    local dest=$1 url=$2 md5=$3
    [[ -f $dest.verified ]] && return
    echo "downloading $url"
    if [[ $url == *.gz ]]; then
        curl -fsSL --retry 5 -C - -o "$dest.gz" "$url"
        gzip -dc "$dest.gz" | dd of="$dest" bs=1M iflag=fullblock conv=sparse status=none
    else
        curl -fsSL --retry 5 -C - -o "$dest" "$url"
    fi
    echo "$md5  $dest" | md5sum -c --quiet
    rm -f "$dest.gz"
    touch "$dest.verified"
}

mkdir -p "$THIRD_PARTY/resources"
cd "$THIRD_PARTY"

checkout mcpat https://github.com/HewlettPackard/mcpat 74d4759f3ba2dff8f5a69e07a68efdb46b42fb8c
build mcpat mcpat make "-j$JOBS"

checkout hotspot https://github.com/uvahotspot/HotSpot v7.0
build hotspot hotspot make "-j$JOBS"

checkout gem5 https://github.com/gem5/gem5 v25.1.0.1
build gem5 build/X86/gem5.opt scons build/X86/gem5.opt "-j$JOBS" --ignore-style

fetch resources/vmlinux-4.19.83 \
    https://dist.gem5.org/dist/develop/kernels/x86/static/vmlinux-4.19.83 \
    28228eac2eea81ee3ce36305b7641c18
fetch resources/parsec.img \
    https://dist.gem5.org/dist/develop/images/x86/ubuntu-18-04/parsec.img.gz \
    19131e62c5b10f137948ec5975391477

touch "$THIRD_PARTY/.installed"
