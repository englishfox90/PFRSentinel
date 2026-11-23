using System.ComponentModel.Composition;
using System.Windows;

namespace PFRSentinel.NINA.SentinelSequenceItems {

    [Export(typeof(ResourceDictionary))]
    public partial class SentinelConditionsTemplates : ResourceDictionary {

        public SentinelConditionsTemplates() {
            InitializeComponent();
        }
    }
}
