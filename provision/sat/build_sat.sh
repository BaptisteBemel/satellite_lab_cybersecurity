#!/usr/bin/env bash
set -euo pipefail

echo "Building NOS3"

cd /home/vagrant/nos3

# Le provisionnement s'exécute en root, mais les sources et la compilation
# appartiennent à l'utilisateur vagrant.
chown -R vagrant:vagrant /home/vagrant/nos3

# Sélectionner la communication UDP directe entre le simulateur radio
# et Yamcs. Ce paramètre choisit le transport réseau :
# il ne désactive pas, à lui seul, SDLS dans le récepteur CI.
echo "Selecting direct UDP communication with Yamcs"
radio_config=components/generic_radio/sim/CMakeLists.txt

if grep -Fq 'set(gsw_flag 1)' "$radio_config"; then
  # Une modification du simulateur nécessite une nouvelle compilation.
  # Supprimer le marqueur AVANT la modification permet de réessayer
  # au prochain provisionnement si une étape échoue.
  rm -f .vagrant_make_done

  sudo -u vagrant sed -i \
    's/set(gsw_flag 1)/set(gsw_flag 0)/' \
    "$radio_config"
fi

# Les sources personnalisées sont conservées dans le dépôt NOS3.
# CI étant un sous-module Git distinct, ses fichiers personnalisés
# doivent être copiés dans le répertoire utilisé par sa compilation.
clear_source=mission-overrides/ci/udp_tf_clear
clear_target=fsw/apps/ci/fsw/examples/udp_tf_clear
clear_files=(ci_custom.c ci_platform_cfg.h MISSION_ci_types.h)

# Vérifier la présence des trois fichiers avant de commencer leur copie.
# Une absence indique que le dépôt NOS3 récupéré ne contient pas
# les sources attendues pour notre baseline.
for clear_file in "${clear_files[@]}"; do
  if [ ! -f "$clear_source/$clear_file" ]; then
    echo "ERROR: missing clear CI source: $clear_source/$clear_file" >&2
    exit 1
  fi
done

sudo -u vagrant mkdir -p "$clear_target"

for clear_file in "${clear_files[@]}"; do
  # Ne recopier que les fichiers absents ou différents.
  # Un provisionnement répété laisse les copies identiques intactes.
  if ! cmp -s "$clear_source/$clear_file" "$clear_target/$clear_file"; then
    # Le code de CI change : le précédent marqueur de compilation
    # ne doit plus permettre de conserver un ancien ci.so.
    rm -f .vagrant_make_done

    # Copier le fichier avec vagrant comme propriétaire.
    # 0644 autorise sa lecture sans le rendre exécutable.
    sudo -u vagrant install -m 0644 \
      "$clear_source/$clear_file" \
      "$clear_target/$clear_file"
  fi
done

# Effectuer la préparation initiale seulement si elle n'a pas déjà
# été enregistrée comme terminée.
if [ ! -f .vagrant_make_prep_done ]; then
  echo "Running make prep"

  # script fournit le terminal attendu par les commandes NOS3.
  # Son option -e transmet le code de sortie de la commande exécutée.
  sudo -iu vagrant script -qefc \
    'cd /home/vagrant/nos3 && make prep' \
    /dev/null

  sudo -u vagrant touch .vagrant_make_prep_done
else
  echo "make prep already done, skipping"
fi

# Compiler lors de la première installation ou après une modification
# détectée plus haut dans les sources CI ou le paramètre radio.
if [ ! -f .vagrant_make_done ]; then
  echo "Running make config and make"

  # Régénérer cfg/build depuis la configuration source avant de compiler.
  # && empêche la compilation si make config échoue.
  sudo -iu vagrant script -qefc \
    'cd /home/vagrant/nos3 && make config && make' \
    /dev/null

  # Enregistrer le succès uniquement après la fin des deux commandes.
  sudo -u vagrant touch .vagrant_make_done
else
  echo "make already done, skipping"
fi

# Mettre les composants NOS3 à disposition du provisionnement de MC
# via le dossier partagé entre le PC hôte et les VM.
echo "Exporting NOS3 components"
tar -czf /vagrant/nos3-components.tar.gz components
ls -lh /vagrant/nos3-components.tar.gz
